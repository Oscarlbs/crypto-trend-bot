"""Estrategia: función pura precios -> pesos objetivo. Sin estado ni E/S."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config


def trend_votes(closes: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Fracción (0 a 1) de reglas que ven tendencia, por activo y día.

    Cada regla es una pareja (media de N días, momentum a M días) y vota "dentro" si el cierre
    supera la media y el retorno a M días es positivo. Sin datos suficientes, vota "fuera".
    """
    votes = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    for sma_days in cfg.sma_grid:
        above = closes > closes.rolling(sma_days).mean()
        for mom_days in cfg.mom_grid:
            votes += (above & (closes / closes.shift(mom_days) - 1.0 > 0)).astype(float)
    return votes / len(cfg.rules)


def volatility(closes: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    return closes.pct_change().rolling(cfg.vol_days).std() * np.sqrt(365)


def target_weights(closes: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Peso objetivo por activo y día, usando solo datos hasta el cierre de ese día.

    Tamaño completo = target_vol / volatilidad realizada, con tope por activo. Se multiplica
    por la fracción de reglas que ven tendencia y se limita a que la suma sea <= 1.
    El resto queda en EUR.
    """
    size = (cfg.target_vol / volatility(closes, cfg)).clip(upper=cfg.max_weight)
    w = (size * trend_votes(closes, cfg)).fillna(0.0)

    total = w.sum(axis=1)
    scale = (1.0 / total.where(total > 0, 1.0)).clip(upper=1.0)
    return w.mul(scale, axis=0)


def latest_target(closes: pd.DataFrame, cfg: Config) -> dict[str, float]:
    if len(closes) < cfg.warmup:
        raise ValueError(f"Hacen falta {cfg.warmup} velas y hay {len(closes)}")
    row = target_weights(closes, cfg).iloc[-1]
    return {s: float(row[s]) for s in cfg.symbols}


def explain(closes: pd.DataFrame, cfg: Config) -> dict[str, dict]:
    """Por qué el objetivo de hoy es el que es: cada regla, la volatilidad y el tamaño."""
    if len(closes) < cfg.warmup:
        raise ValueError(f"Hacen falta {cfg.warmup} velas y hay {len(closes)}")
    weights = target_weights(closes, cfg).iloc[-1]
    vol = volatility(closes, cfg).iloc[-1]
    out = {}
    for s in cfg.symbols:
        px = closes[s]
        last = float(px.iloc[-1])
        rules = []
        for sma_days, mom_days in cfg.rules:
            sma = float(px.iloc[-sma_days:].mean())
            mom = last / float(px.iloc[-1 - mom_days]) - 1.0
            rules.append({
                "sma": sma_days, "mom": mom_days,
                "sobre_media": last > sma, "dist_media": last / sma - 1.0,
                "sube": mom > 0, "momentum": mom,
                "dentro": last > sma and mom > 0,
            })
        yes = sum(r["dentro"] for r in rules)
        out[s] = {
            "cierre": last,
            "reglas": rules,
            "votos": yes,
            "total": len(rules),
            "volatilidad": float(vol[s]),
            "tamano_completo": float(min(cfg.target_vol / vol[s], cfg.max_weight)) if vol[s] > 0 else 0.0,
            "peso": float(weights[s]),
        }
    return out
