"""Backtest diario sin look-ahead, neto de comisiones y slippage.

Señal con el cierre del día t -> órdenes a la apertura del día t+1.
Uso:  python -m bot.backtest [--since 2020-01-01] [--symbols BTC/EUR ETH/EUR] [--plot]
"""
from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from .broker import simulate_fill
from .config import Config, from_env
from .risk import Order, plan_orders
from .strategy import target_weights


def run_backtest(opens: pd.DataFrame, closes: pd.DataFrame, cfg: Config, start: int | None = None) -> dict:
    weights = target_weights(closes, cfg)
    start = cfg.warmup if start is None else start
    if len(closes) < start + 30:
        raise ValueError("Histórico demasiado corto para este backtest")

    syms = list(cfg.symbols)
    w, px_o, px_c = weights[syms].to_numpy(), opens[syms].to_numpy(), closes[syms].to_numpy()
    cash = cfg.budget
    pos = {s: 0.0 for s in syms}
    curve, invested = [cash], [0.0]
    trades, fees = 0, 0.0

    for i in range(start, len(closes) - 1):
        target = {s: float(w[i, k]) for k, s in enumerate(syms)}
        px_open = {s: float(px_o[i + 1, k]) for k, s in enumerate(syms)}
        for order in plan_orders(target, pos, px_open, cash, cfg):
            fill = simulate_fill(order, px_open[order.symbol], cfg)
            sign = 1 if fill.side == "buy" else -1
            pos[fill.symbol] = max(0.0, pos[fill.symbol] + sign * fill.qty)
            cash += fill.cash_delta
            fees += fill.fee
            trades += 1
        held = sum(pos[s] * float(px_c[i + 1, k]) for k, s in enumerate(syms))
        eq = cash + held
        curve.append(eq)
        invested.append(held / eq if eq > 0 else 0.0)

    return {
        "equity": pd.Series(curve, index=closes.index[start:], name="estrategia"),
        "trades": trades,
        "fees": fees,
        "invested": float(np.mean(invested)),
    }


def buy_and_hold(opens: pd.DataFrame, closes: pd.DataFrame, cfg: Config, weights: dict[str, float], start: int) -> pd.Series:
    """Compra una vez a la primera apertura (con costes) y mantiene."""
    qty = {}
    cash = cfg.budget
    for s, w in weights.items():
        fill = simulate_fill(Order(s, "buy", value=cfg.budget * w), float(opens.iloc[start + 1][s]), cfg)
        qty[s] = fill.qty
        cash += fill.cash_delta
    curve = [cfg.budget] + [
        cash + sum(q * float(closes.iloc[i][s]) for s, q in qty.items())
        for i in range(start + 1, len(closes))
    ]
    return pd.Series(curve, index=closes.index[start:])


def metrics(eq: pd.Series) -> dict:
    rets = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    total = eq.iloc[-1] / eq.iloc[0] - 1
    std = rets.std()
    return {
        "total": total,
        "cagr": (1 + total) ** (1 / years) - 1 if total > -1 else -1.0,
        "vol": std * np.sqrt(365),
        "sharpe": float(rets.mean() / std * np.sqrt(365)) if std > 0 else 0.0,
        "maxdd": float((eq / eq.cummax() - 1).min()),
    }


def yearly_returns(eq: pd.Series) -> pd.Series:
    last = eq.groupby(eq.index.year).last()
    prev = last.shift(1)
    prev.iloc[0] = eq.iloc[0]
    return last / prev - 1


def _row(name: str, m: dict) -> str:
    return (f"{name:<22}{m['total']:>10.1%}{m['cagr']:>9.1%}{m['vol']:>8.1%}"
            f"{m['sharpe']:>8.2f}{m['maxdd']:>9.1%}")


def analyze(opens: pd.DataFrame, closes: pd.DataFrame, cfg: Config) -> dict:
    """Todo lo que cuenta el informe, en datos: curvas, métricas, cada regla suelta y veredicto."""
    start = cfg.warmup  # mismo punto de arranque para todas las variantes, para que sean comparables
    res = run_backtest(opens, closes, cfg, start=start)
    eq = res["equity"]
    even = {s: 1 / len(cfg.symbols) for s in cfg.symbols}
    bh = buy_and_hold(opens, closes, cfg, even, start)
    first = cfg.symbols[0]
    bh_first = buy_and_hold(opens, closes, cfg, {first: 1.0}, start)
    m, mb, mf = metrics(eq), metrics(bh), metrics(bh_first)

    single = {}
    for a in cfg.sma_grid:
        for b in cfg.mom_grid:
            one = replace(cfg, sma_grid=(a,), mom_grid=(b,))
            single[(a, b)] = metrics(run_backtest(opens, closes, one, start=start)["equity"])
    better = sum(mm["sharpe"] > mb["sharpe"] for mm in single.values())

    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    half = len(eq) // 2
    below = eq[eq < cfg.kill_floor]
    checks = {
        "sharpe": m["sharpe"] > mb["sharpe"],
        "caida": abs(m["maxdd"]) < 0.7 * abs(mb["maxdd"]),
        "robusto": better >= 0.75 * len(single),
    }
    return {
        "equity": eq, "bh": bh, "bh_first": bh_first, "first": first,
        "metrics": m, "metrics_bh": mb, "metrics_first": mf,
        "trades": res["trades"], "trades_month": res["trades"] / max(len(eq) / 30.4, 1),
        "fees": res["fees"], "fee_drag": res["fees"] / eq.mean() / years, "invested": res["invested"],
        "kill": (below.index[0], float(below.iloc[0])) if len(below) else None,
        "yearly": yearly_returns(eq), "yearly_bh": yearly_returns(bh),
        "halves": [(metrics(a), metrics(b)) for a, b in ((eq.iloc[:half], bh.iloc[:half]), (eq.iloc[half:], bh.iloc[half:]))],
        "single": single, "better": better,
        "checks": checks, "passed": all(checks.values()),
    }


def report(opens: pd.DataFrame, closes: pd.DataFrame, cfg: Config) -> tuple[str, pd.DataFrame, bool]:
    a = analyze(opens, closes, cfg)
    eq, bh, first = a["equity"], a["bh"], a["first"]
    out = []
    out.append(f"BACKTEST {' + '.join(cfg.symbols)}  |  {eq.index[0].date()} -> {eq.index[-1].date()}")
    out.append(f"{len(cfg.rules)} reglas (medias {'/'.join(map(str, cfg.sma_grid))}d x momentum "
               f"{'/'.join(map(str, cfg.mom_grid))}d), vol objetivo {cfg.target_vol:.0%}, "
               f"tope {cfg.max_weight:.0%}, banda {cfg.rebalance_band:.0%}, "
               f"comisión {cfg.fee_rate:.2%} + slippage {cfg.slippage:.2%} por lado\n")
    out.append(f"{'':<22}{'Total':>10}{'CAGR':>9}{'Vol':>8}{'Sharpe':>8}{'Max DD':>9}")
    out.append(_row("Estrategia", a["metrics"]))
    out.append(_row("Comprar y mantener", a["metrics_bh"]))
    out.append(_row(f"Solo {first}", a["metrics_first"]))
    out.append("")
    out.append(f"Operaciones: {a['trades']} ({a['trades_month']:.1f}/mes)  |  "
               f"Comisiones: {a['fees']:.2f} EUR en total (~{a['fee_drag']:.1%} anual del capital medio)  |  "
               f"Tiempo invertido: {a['invested']:.0%}")
    out.append("Kill switch: " + (f"habría saltado el {a['kill'][0].date()} (equity {a['kill'][1]:.2f})"
                                  if a["kill"] else f"no habría saltado (mínimo {eq.min():.2f} EUR)"))

    out.append("\nPor año (estrategia vs comprar y mantener):")
    for year in a["yearly"].index:
        out.append(f"  {year}  {a['yearly'][year]:>8.1%}  vs {a['yearly_bh'][year]:>8.1%}")

    out.append("\nMitades del periodo (comprobación fuera de muestra, parámetros fijos):")
    for label, (ma, mbh) in zip(("1ª mitad", "2ª mitad"), a["halves"]):
        out.append(f"  {label}: Sharpe {ma['sharpe']:.2f} vs {mbh['sharpe']:.2f} | "
                   f"Max DD {ma['maxdd']:.1%} vs {mbh['maxdd']:.1%}")

    out.append("\nCada regla por separado (Sharpe / Max DD). El bot usa el voto de todas:")
    out.append("  SMA\\MOM " + "".join(f"{b:>16}" for b in cfg.mom_grid))
    for sma in cfg.sma_grid:
        cells = [f"{a['single'][(sma, b)]['sharpe']:>7.2f} /{a['single'][(sma, b)]['maxdd']:>6.0%}" for b in cfg.mom_grid]
        out.append(f"  {sma:>7} " + "".join(f"{c:>16}" for c in cells))

    ok = a["checks"]
    out.append("\nCRITERIO (neto de costes):")
    out.append(f"  [{'x' if ok['sharpe'] else ' '}] Sharpe mayor que comprar y mantener")
    out.append(f"  [{'x' if ok['caida'] else ' '}] Caída máxima al menos un 30 % menor")
    out.append(f"  [{'x' if ok['robusto'] else ' '}] Robusto: {a['better']}/{len(a['single'])} reglas sueltas superan el Sharpe de comprar y mantener")
    out.append("VEREDICTO: " + ("PASA -> siguiente fase: paper" if a["passed"] else "NO PASA -> no poner dinero con esta configuración"))
    out.append("Rentabilidades pasadas no garantizan nada; esto solo descarta lo que no funciona.")

    curves = pd.concat({"estrategia": eq, "comprar_y_mantener": bh, f"solo_{first}": a["bh_first"]}, axis=1)
    return "\n".join(out), curves, a["passed"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest de trendbot")
    ap.add_argument("--since", default="2020-01-01")
    ap.add_argument("--symbols", nargs="+")
    ap.add_argument("--refresh", action="store_true", help="vuelve a descargar los datos")
    ap.add_argument("--plot", action="store_true", help="guarda reports/backtest.png (requiere matplotlib)")
    args = ap.parse_args()

    from .data import history

    cfg = from_env()
    if args.symbols:
        cfg = replace(cfg, symbols=tuple(args.symbols))
    opens, closes = history(cfg, args.since, refresh=args.refresh)
    text, curves, _ = report(opens, closes, cfg)
    print(text)

    out_dir = cfg.root / "reports"
    out_dir.mkdir(exist_ok=True)
    curves.to_csv(out_dir / "backtest_equity.csv")
    (out_dir / "backtest.txt").write_text(text + "\n")
    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        ax = curves.plot(logy=True, figsize=(10, 5), title="trendbot: equity (escala log)")
        ax.set_ylabel("EUR")
        plt.tight_layout()
        plt.savefig(out_dir / "backtest.png", dpi=130)
    print(f"\nGuardado en {out_dir}/")


if __name__ == "__main__":
    main()
