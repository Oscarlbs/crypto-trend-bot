import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SYMBOLS = ("BTC/EUR", "ETH/EUR")


def make_prices(n=900, seed=7, end="2025-12-31", drifts=(0.004, -0.004, 0.003)):
    """Precios sintéticos deterministas con tres regímenes (sube, baja, sube)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=end, periods=n, freq="D")
    third = n // 3
    drift = np.concatenate([
        np.full(third, drifts[0]), np.full(third, drifts[1]), np.full(n - 2 * third, drifts[2]),
    ])
    closes = {}
    for k, s in enumerate(SYMBOLS):
        r = drift + rng.normal(0, 0.02 * (1 + 0.5 * k), n)
        closes[s] = 100 * (k + 1) * np.exp(np.cumsum(r))
    closes = pd.DataFrame(closes, index=idx)
    opens = closes.shift(1).fillna(closes.iloc[0])
    return opens, closes


@pytest.fixture
def prices():
    return make_prices()


class FakeTicker:
    """Exchange mínimo para PaperBroker: solo precios."""

    def __init__(self, prices):
        self.prices = dict(prices)

    def fetch_ticker(self, symbol):
        return {"last": self.prices[symbol]}


class FakeExchange(FakeTicker):
    """Imita lo que usa LiveBroker de ccxt/Binance: comisión de compra en el activo base."""

    FEE = 0.001

    def __init__(self, prices, eur=100.0, fail_on=None):
        super().__init__(prices)
        self.bal = {"EUR": eur, "BTC": 0.0, "ETH": 0.0}
        self.orders = []
        self.fail_on = fail_on

    def market(self, symbol):
        return {"limits": {"cost": {"min": 5.0}}}

    def amount_to_precision(self, symbol, amount):
        return f"{math.floor(amount * 1e5) / 1e5:.5f}"

    def fetch_balance(self):
        return {"free": dict(self.bal)}

    def create_market_buy_order_with_cost(self, symbol, cost):
        if self.fail_on == "buy":
            raise ConnectionError("timeout")
        base = symbol.split("/")[0]
        assert cost <= self.bal["EUR"] + 1e-9, "saldo insuficiente"
        filled = cost / self.prices[symbol]
        fee = filled * self.FEE
        self.bal["EUR"] -= cost
        self.bal[base] += filled - fee
        self.orders.append(("buy", symbol, cost))
        return {"id": str(len(self.orders)), "status": "closed", "filled": filled, "cost": cost,
                "fees": [{"currency": base, "cost": fee}]}

    def create_market_sell_order(self, symbol, amount):
        base = symbol.split("/")[0]
        assert amount <= self.bal[base] + 1e-12, "saldo insuficiente"
        cost = amount * self.prices[symbol]
        fee = cost * self.FEE
        self.bal[base] -= amount
        self.bal["EUR"] += cost - fee
        self.orders.append(("sell", symbol, amount))
        return {"id": str(len(self.orders)), "status": "closed", "filled": amount, "cost": cost,
                "fees": [{"currency": "EUR", "cost": fee}]}
