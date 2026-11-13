"""Job diario. Idempotente: se puede lanzar cada hora y solo actúa una vez por vela diaria.

Uso:  python -m bot.run [--dry-run] [--force] [--status]
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime, timedelta, timezone

from . import state as store
from .broker import LiveBroker, PaperBroker
from .config import Config, from_env
from .notify import notify as default_notify
from .risk import equity, liquidation_orders, plan_orders, should_kill
from .strategy import explain


def _apply(st: store.State, fill) -> None:
    sign = 1 if fill.side == "buy" else -1
    st.positions[fill.symbol] = max(0.0, st.positions.get(fill.symbol, 0.0) + sign * fill.qty)
    st.cash += fill.cash_delta


def _check_ledger(cfg: Config, st: store.State, free: dict | None) -> None:
    """En real, el libro del bot nunca puede superar lo que hay libre en la cuenta."""
    if free is None:
        return
    quote = cfg.symbols[0].split("/")[1]
    if st.cash > float(free.get(quote) or 0) + 0.01:
        raise RuntimeError(f"El libro dice {st.cash:.2f} {quote} y la cuenta tiene {float(free.get(quote) or 0):.2f} libres")
    for s, qty in st.positions.items():
        base = s.split("/")[0]
        have = float(free.get(base) or 0)
        if qty > have * (1 + 1e-6) + 1e-9:
            raise RuntimeError(f"El libro dice {qty:.8f} {base} y la cuenta tiene {have:.8f} libres")


def _execute(cfg, st, broker, orders, prices, candle, reason, notifier) -> list[str]:
    lines = []
    for order in orders:
        try:
            fill = broker.execute(order, prices[order.symbol])
        except Exception as exc:
            # Estado incierto con dinero real: parar y revisar a mano.
            if cfg.mode == "live":
                st.halted, st.halt_reason = True, f"Fallo al ejecutar {order.side} {order.symbol}: {exc}"
                store.save(cfg, st)
                notifier(f"trendbot [live] PARADO. {st.halt_reason}\nRevisa la cuenta y el libro antes de reactivar.")
            raise
        if fill is None:
            continue
        _apply(st, fill)
        store.save(cfg, st)  # tras cada fill, por si el proceso muere a mitad
        store.log_trade(cfg, candle, fill, reason)
        lines.append(f"{fill.side.upper()} {fill.symbol} {abs(fill.cash_delta):.2f} EUR @ {fill.price:.2f}")
    return lines


def run(cfg: Config, *, force=False, dry_run=False, fetch=None, broker=None, notifier=None, now=None) -> str:
    notifier = notifier or default_notify
    now = now or datetime.now(timezone.utc)
    st = store.load(cfg)
    tag = f"trendbot [{cfg.mode}]"

    if st.halted:
        # Recordatorio una vez al día hasta que se reactive. Si el aviso no llega
        # (sin red), se reintenta en la siguiente pasada.
        today = now.strftime("%Y-%m-%d")
        if st.halt_alerted != today:
            sent = notifier(f"{tag} sigue PARADO: {st.halt_reason}\nReactívalo a mano cuando lo revises.")
            if sent is not False:
                st.halt_alerted = today
                store.save(cfg, st)
        else:
            print(f"{tag} parado: {st.halt_reason}")
        return "halted"

    if fetch is None:
        from .data import recent_closes

        fetch = recent_closes
    closes = fetch(cfg)
    candle = closes.index[-1].strftime("%Y-%m-%d")
    expected = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if candle < expected:
        raise RuntimeError(f"Datos desfasados: última vela {candle}, esperada {expected}")

    if st.last_candle == candle and not force:
        print(f"{tag} vela {candle} ya procesada")
        return "skipped"

    broker = broker or (LiveBroker(cfg) if cfg.mode == "live" else PaperBroker(cfg))
    prices = {s: broker.price(s) for s in cfg.symbols}
    eq = equity(st.cash, st.positions, prices)
    if cfg.mode == "live" and not dry_run:
        _check_ledger(cfg, st, broker.free())

    # Kill switch: por debajo del suelo, vender todo y no volver a operar.
    if should_kill(eq, cfg):
        if dry_run:
            print(f"{tag} [dry-run] saltaría el kill switch (equity {eq:.2f} < {cfg.kill_floor:.2f})")
            return "dry-run"
        lines = _execute(cfg, st, broker, liquidation_orders(st.positions, prices), prices, candle, "kill", notifier)
        st.halted = True
        st.halt_reason = f"Kill switch: equity {eq:.2f} < suelo {cfg.kill_floor:.2f} ({candle})"
        st.last_candle = candle
        store.save(cfg, st)
        store.log_equity(cfg, candle, equity(st.cash, st.positions, prices), st)
        notifier(f"{tag} KILL SWITCH {candle}\n{st.halt_reason}\n" + "\n".join(lines))
        return "killed"

    why = explain(closes, cfg)
    target = {s: why[s]["peso"] for s in cfg.symbols}
    orders = plan_orders(target, st.positions, prices, st.cash, cfg)
    weights_txt = " · ".join(
        f"{s.split('/')[0]} {why[s]['peso']:.0%} ({why[s]['votos']}/{why[s]['total']} reglas)" for s in cfg.symbols
    )

    if dry_run:
        print(f"{tag} [dry-run] {candle} equity {eq:.2f} | objetivo {weights_txt}")
        for o in orders:
            print(f"  {o.side.upper()} {o.symbol} ~{o.value:.2f} EUR")
        return "dry-run"

    lines = _execute(cfg, st, broker, orders, prices, candle, "rebalance", notifier)
    eq = equity(st.cash, st.positions, prices)
    st.last_candle = candle
    st.peak_equity = max(st.peak_equity, eq)
    store.save(cfg, st)
    store.log_equity(cfg, candle, eq, st)
    notifier(
        f"{tag} {candle}\n"
        f"Equity: {eq:.2f} EUR ({eq / cfg.budget - 1:+.1%} desde inicio)\n"
        f"Objetivo: {weights_txt}\n"
        + ("\n".join(lines) if lines else "Sin operaciones")
    )
    return "ok"


def status(cfg: Config) -> None:
    st = store.load(cfg)
    print(f"Modo {cfg.mode} | presupuesto {cfg.budget:.2f} | suelo kill {cfg.kill_floor:.2f}")
    print(f"Caja: {st.cash:.2f} | última vela: {st.last_candle} | parado: {st.halted} {st.halt_reason or ''}")
    for s, q in st.positions.items():
        print(f"  {s}: {q:.8f}")
    trades = cfg.state_dir / "trades.csv"
    if trades.exists():
        print("\nÚltimas operaciones:")
        print("".join(trades.read_text().splitlines(keepends=True)[-6:]))


def main() -> None:
    ap = argparse.ArgumentParser(description="Job diario de trendbot")
    ap.add_argument("--dry-run", action="store_true", help="calcula y muestra las órdenes sin ejecutarlas")
    ap.add_argument("--force", action="store_true", help="procesa aunque la vela ya esté hecha")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    cfg = from_env()
    if args.status:
        return status(cfg)
    try:
        run(cfg, force=args.force, dry_run=args.dry_run)
    except Exception as exc:
        traceback.print_exc()
        default_notify(f"trendbot [{cfg.mode}] ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
