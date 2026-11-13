"""Estado persistente: libro propio del bot (JSON) + registros de operaciones y equity (CSV)."""
from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .config import Config


@dataclass
class State:
    cash: float
    positions: dict[str, float] = field(default_factory=dict)
    last_candle: str | None = None
    halted: bool = False
    halt_reason: str | None = None
    halt_alerted: str | None = None  # último día en que el aviso de paro llegó
    peak_equity: float = 0.0
    started: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(cfg: Config) -> State:
    path = cfg.state_dir / "state.json"
    if path.exists():
        st = State(**json.loads(path.read_text()))
    else:
        st = State(cash=cfg.budget, peak_equity=cfg.budget, started=_now())
    for s in cfg.symbols:
        st.positions.setdefault(s, 0.0)
    return st


def save(cfg: Config, st: State) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.state_dir / "state.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(st), indent=2))
    os.replace(tmp, path)  # escritura atómica


def _append(cfg: Config, name: str, row: dict) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.state_dir / name
    new = not path.exists()
    with path.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row))
        if new:
            writer.writeheader()
        writer.writerow(row)


def log_trade(cfg: Config, candle: str, fill, reason: str = "rebalance") -> None:
    _append(cfg, "trades.csv", {
        "ts": _now(), "candle": candle, "symbol": fill.symbol, "side": fill.side,
        "qty": f"{fill.qty:.8f}", "price": f"{fill.price:.2f}",
        "cash_delta": f"{fill.cash_delta:.4f}", "fee_eur": f"{fill.fee:.4f}",
        "order_id": fill.order_id, "reason": reason,
    })


def log_equity(cfg: Config, candle: str, eq: float, st: State) -> None:
    _append(cfg, "equity.csv", {
        "ts": _now(), "candle": candle, "equity": f"{eq:.4f}", "cash": f"{st.cash:.4f}",
        **{s: f"{q:.8f}" for s, q in st.positions.items()},
    })
