"""Configuración. Los parámetros de estrategia y el dinero de la prueba viven aquí; modo, presupuesto real y claves, en .env."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> None:
    """Carga KEY=VALUE de .env sin pisar variables ya definidas."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), _value(value))


def _value(raw: str) -> str:
    """Valor de una línea de .env: sin comillas y sin el comentario del final (`paper   # texto`)."""
    raw = raw.strip()
    if raw and raw[0] in "\"'":
        end = raw.find(raw[0], 1)
        if end > 0:
            return raw[1:end]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


@dataclass(frozen=True)
class Config:
    # Universo
    exchange: str = "binance"
    symbols: tuple[str, ...] = ("BTC/EUR", "ETH/EUR")

    # Señal: votan todas las parejas (media, momentum). Una pareja vota "dentro" si el cierre
    # supera su media de N días y el retorno a M días es positivo. La posición es la fracción de
    # votos a favor. Así el resultado no depende de haber acertado con una sola pareja de números.
    sma_grid: tuple[int, ...] = (50, 100, 150, 200)
    mom_grid: tuple[int, ...] = (20, 30, 60)

    # Tamaño: volatilidad objetivo anualizada por activo, con tope por activo
    vol_days: int = 30
    target_vol: float = 0.25
    max_weight: float = 0.50

    # Ejecución
    rebalance_band: float = 0.10  # solo opera si el peso cambia más de esto
    min_order: float = 10.0       # EUR; por debajo no se manda la orden
    fee_rate: float = 0.001       # 0,10 % por lado
    slippage: float = 0.0005      # 0,05 % asumido en simulación

    # Riesgo
    budget: float = 100.0         # EUR reales que gestiona el bot en live (su libro propio)
    paper_budget: float = 10_000.0  # EUR de mentira con los que arranca la prueba (paper)
    kill_fraction: float = 0.75   # si equity < budget * esto: vende todo y se para

    # Entorno
    mode: str = "paper"           # paper | live
    root: Path = ROOT

    @property
    def kill_floor(self) -> float:
        return self.budget * self.kill_fraction

    @property
    def state_dir(self) -> Path:
        return self.root / "state" / self.mode

    @property
    def rules(self) -> list[tuple[int, int]]:
        return [(a, b) for a in self.sma_grid for b in self.mom_grid]

    @property
    def warmup(self) -> int:
        return max(*self.sma_grid, *self.mom_grid, self.vol_days) + 1


def from_env() -> Config:
    load_env()
    cfg = Config()
    mode = os.environ.get("BOT_MODE", "paper").lower()
    if mode not in ("paper", "live"):
        raise ValueError(f"BOT_MODE debe ser paper o live, no {mode!r}")
    # BOT_BUDGET es dinero de verdad y solo cuenta en live: la prueba usa siempre paper_budget,
    # así subir la cifra de la demo no cambia cuánto se arriesga el día que se pase a real.
    budget = cfg.paper_budget if mode == "paper" else float(os.environ.get("BOT_BUDGET", cfg.budget))
    return replace(cfg, mode=mode, budget=budget)
