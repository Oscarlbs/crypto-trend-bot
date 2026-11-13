"""Riesgo y planificación de órdenes. Lo usan igual el backtest y el job real."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config


@dataclass
class Order:
    symbol: str
    side: str            # buy | sell
    value: float         # EUR aproximados de la orden
    qty: float = 0.0     # unidades del activo (solo ventas)


def equity(cash: float, positions: dict[str, float], prices: dict[str, float]) -> float:
    return cash + sum(positions.get(s, 0.0) * p for s, p in prices.items())


def should_kill(eq: float, cfg: Config) -> bool:
    return eq < cfg.kill_floor


def liquidation_orders(positions: dict[str, float], prices: dict[str, float]) -> list[Order]:
    return [
        Order(s, "sell", value=q * prices[s], qty=q)
        for s, q in positions.items()
        if q > 0
    ]


def plan_orders(
    target: dict[str, float],
    positions: dict[str, float],
    prices: dict[str, float],
    cash: float,
    cfg: Config,
) -> list[Order]:
    """Órdenes para ir de la cartera actual a los pesos objetivo. Ventas primero."""
    values = {s: positions.get(s, 0.0) * prices[s] for s in cfg.symbols}
    eq = cash + sum(values.values())
    if eq <= 0:
        return []

    sells: list[Order] = []
    buys: list[Order] = []
    for s in cfg.symbols:
        tgt = target.get(s, 0.0)
        cur = values[s] / eq
        delta = tgt - cur

        if tgt == 0.0 and values[s] > 0:
            # Salida completa: siempre, sin banda (si supera el mínimo operable).
            if values[s] >= cfg.min_order:
                sells.append(Order(s, "sell", value=values[s], qty=positions[s]))
            continue
        if abs(delta) < cfg.rebalance_band:
            continue
        value = abs(delta) * eq
        if value < cfg.min_order:
            continue
        if delta < 0:
            qty = min(positions[s], value / prices[s])
            sells.append(Order(s, "sell", value=qty * prices[s], qty=qty))
        else:
            buys.append(Order(s, "buy", value=value))

    # Las compras no pueden superar la caja disponible tras las ventas (con colchón).
    cash_after = cash + sum(o.value for o in sells) * (1 - cfg.fee_rate - cfg.slippage)
    available = max(0.0, cash_after * 0.998)
    need = sum(o.value for o in buys)
    if need > available and need > 0:
        k = available / need
        buys = [Order(o.symbol, "buy", value=o.value * k) for o in buys]
        buys = [o for o in buys if o.value >= cfg.min_order]

    return sells + buys
