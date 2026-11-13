"""Foto del bot para el panel BananaTrading: cuenta, mercado, reglas, prueba y backtest.

Solo lee: no manda órdenes ni toca el libro del bot. Escribe reports/dashboard.json y lo imprime.
Uso:  python -m bot.dashboard
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import state as store
from .backtest import analyze
from .config import Config, from_env
from .risk import equity, plan_orders
from .strategy import explain, latest_target, target_weights, trend_votes

SINCE = "2020-01-01"   # desde cuándo se descarga el histórico del backtest
CHART_DAYS = 420       # días de precio que enseña el panel
VERSION = 1


def _num(x, digits: int = 4):
    """Número apto para JSON: redondeado, y None si no hay dato."""
    x = float(x)
    return None if not np.isfinite(x) else round(x, digits)


def _list(values, digits: int = 2) -> list:
    return [_num(v, digits) for v in values]


def _days(index) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in index]


def _rows(path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def _fresh_history(cfg: Config, now: datetime, fetch=None):
    """Histórico diario al día: si la caché no llega a la vela de ayer, se vuelve a descargar."""
    if fetch is None:
        from .data import history as fetch
    opens, closes = fetch(cfg, SINCE)
    if closes.index[-1].strftime("%Y-%m-%d") < (now - timedelta(days=1)).strftime("%Y-%m-%d"):
        opens, closes = fetch(cfg, SINCE, refresh=True)
    return opens, closes


def account(cfg: Config, st: store.State, prices: dict[str, float], last_closed: str, now: datetime) -> dict:
    eq = equity(st.cash, st.positions, prices)
    positions = {}
    for s in cfg.symbols:
        qty = st.positions.get(s, 0.0)
        value = qty * prices[s]
        positions[s] = {"cantidad": _num(qty, 8), "valor": _num(value, 2), "peso": _num(value / eq if eq > 0 else 0)}
    started = datetime.strptime(st.started, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if st.started else now
    return {
        "modo": cfg.mode,
        "presupuesto": cfg.budget,
        "suelo_kill": cfg.kill_floor,
        "equity": _num(eq, 2),
        "resultado": _num(eq / cfg.budget - 1),
        "caja": _num(st.cash, 2),
        "invertido": _num(1 - st.cash / eq if eq > 0 else 0),
        "posiciones": positions,
        "maximo": _num(max(st.peak_equity, eq), 2),
        "inicio": st.started,
        "dias": max(0, (now - started).days),
        "ultima_vela": st.last_candle,
        "al_dia": st.last_candle == last_closed,
        "parado": st.halted,
        "motivo_paro": st.halt_reason,
        "telegram": bool(os.environ.get("TELEGRAM_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")),
    }


def market(cfg: Config, closes: pd.DataFrame, tickers: dict[str, dict]) -> dict:
    why = explain(closes, cfg)
    votes = trend_votes(closes, cfg) * len(cfg.rules)
    weights = target_weights(closes, cfg)
    tail = slice(-CHART_DAYS, None)
    out = {}
    for s in cfg.symbols:
        t = tickers[s]
        last = float(t["last"])
        bid, ask = float(t.get("bid") or 0), float(t.get("ask") or 0)
        px = closes[s]
        out[s] = {
            **why[s],
            "precio": _num(last, 2),
            "cambio_desde_cierre": _num(last / float(px.iloc[-1]) - 1),
            "spread": _num((ask - bid) / ((ask + bid) / 2), 6) if bid > 0 and ask > 0 else None,
            "fechas": _days(px.index[tail]),
            "cierres": _list(px.iloc[tail]),
            "medias": {str(n): _list(px.rolling(n).mean().iloc[tail]) for n in cfg.sma_grid},
            "votos_dia": [int(round(v)) for v in votes[s].iloc[tail]],
            "peso_dia": _list(weights[s].iloc[tail], 4),
        }
        for rule in out[s]["reglas"]:
            rule["dist_media"], rule["momentum"] = _num(rule["dist_media"]), _num(rule["momentum"])
        for key in ("cierre", "volatilidad", "tamano_completo", "peso"):
            out[s][key] = _num(out[s][key], 4 if key != "cierre" else 2)
    return out


def start_luck(eq: pd.Series, floor: float, horizon: int = 365) -> dict:
    """Qué habría pasado empezando cualquier día del histórico (resultado al año y freno de emergencia)."""
    v = eq.to_numpy()
    results, killed = [], 0
    for i in range(len(v) - horizon):
        window = v[i : i + horizon + 1] / v[i]
        results.append(window[-1] - 1)
        killed += bool((window < floor).any())
    if not results:
        return {}
    results = np.array(results)
    return {
        "inicios": len(results),
        "mediana": _num(np.median(results)),
        "peor": _num(results.min()),
        "mejor": _num(results.max()),
        "negativos": _num((results < 0).mean()),
        "freno": _num(killed / len(results)),
    }


def backtest_section(cfg: Config, opens: pd.DataFrame, closes: pd.DataFrame) -> dict:
    """El backtest solo cambia con cada vela nueva: se calcula una vez al día y se guarda."""
    key = f"{VERSION}|{closes.index[-1].date()}|{len(closes)}|{cfg.symbols}|{cfg.rules}|{cfg.target_vol}|{cfg.max_weight}|" \
          f"{cfg.vol_days}|{cfg.rebalance_band}|{cfg.min_order}|{cfg.fee_rate}|{cfg.slippage}|{cfg.budget}|{cfg.kill_fraction}"
    cache = cfg.root / "reports" / "dashboard_backtest.json"
    if cache.exists():
        try:
            saved = json.loads(cache.read_text())
            if saved.get("clave") == key:
                return saved
        except ValueError:
            pass

    a = analyze(opens, closes, cfg)
    eq, bh = a["equity"], a["bh"]
    clean = lambda m: {k: _num(v) for k, v in m.items()}
    section = {
        "clave": key,
        "desde": eq.index[0].strftime("%Y-%m-%d"),
        "hasta": eq.index[-1].strftime("%Y-%m-%d"),
        "fechas": _days(eq.index),
        "estrategia": _list(eq),
        "comprar": _list(bh),
        "metricas": {"estrategia": clean(a["metrics"]), "comprar": clean(a["metrics_bh"])},
        "anual": [{"ano": int(y), "estrategia": _num(a["yearly"][y]), "comprar": _num(a["yearly_bh"][y])} for y in a["yearly"].index],
        "mitades": [{"estrategia": clean(x), "comprar": clean(y)} for x, y in a["halves"]],
        "reglas": [{"sma": k[0], "mom": k[1], "sharpe": _num(m["sharpe"], 2), "maxdd": _num(m["maxdd"])} for k, m in a["single"].items()],
        "reglas_mejores": int(a["better"]),
        "operaciones": int(a["trades"]),
        "operaciones_mes": _num(a["trades_month"], 1),
        "comisiones_anual": _num(a["fee_drag"]),
        "tiempo_invertido": _num(a["invested"]),
        "criterios": {k: bool(v) for k, v in a["checks"].items()},
        "pasa": bool(a["passed"]),
        "suerte": start_luck(eq, cfg.kill_fraction),
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(section, allow_nan=False))
    os.replace(tmp, cache)
    return section


def build(cfg: Config, *, ex=None, fetch=None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    opens, closes = _fresh_history(cfg, now, fetch)
    if ex is None:
        from .data import public_exchange

        ex = public_exchange(cfg)
    tickers = {s: ex.fetch_ticker(s) for s in cfg.symbols}
    prices = {s: float(tickers[s]["last"]) for s in cfg.symbols}

    st = store.load(cfg)
    mkt = market(cfg, closes, tickers)
    orders = [] if st.halted else plan_orders(latest_target(closes, cfg), st.positions, prices, st.cash, cfg)
    return {
        "version": VERSION,
        "generado": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": {
            "simbolos": list(cfg.symbols),
            "medias": list(cfg.sma_grid),
            "momentos": list(cfg.mom_grid),
            "vol_dias": cfg.vol_days,
            "vol_objetivo": cfg.target_vol,
            "tope": cfg.max_weight,
            "banda": cfg.rebalance_band,
            "orden_minima": cfg.min_order,
            "comision": cfg.fee_rate,
            "slippage": cfg.slippage,
            "freno": cfg.kill_fraction,
        },
        "cuenta": account(cfg, st, prices, closes.index[-1].strftime("%Y-%m-%d"), now),
        "mercado": mkt,
        "si_cerrase_ahora": [{"lado": o.side, "simbolo": o.symbol, "euros": _num(o.value, 2)} for o in orders],
        "historial": {
            "equity": [{"vela": r["candle"], "equity": _num(r["equity"], 2), "caja": _num(r["cash"], 2)} for r in _rows(cfg.state_dir / "equity.csv")],
            "operaciones": _rows(cfg.state_dir / "trades.csv")[-200:],
        },
        "backtest": backtest_section(cfg, opens, closes),
    }


def main() -> None:
    cfg = from_env()
    text = json.dumps(build(cfg), allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    out = cfg.root / "reports" / "dashboard.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, out)
    print(text)


if __name__ == "__main__":
    main()
