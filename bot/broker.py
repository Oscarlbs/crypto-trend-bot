"""Ejecución. PaperBroker simula con precios reales; LiveBroker manda órdenes vía ccxt."""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, replace

from .config import Config
from .risk import Order


@dataclass
class Fill:
    symbol: str
    side: str
    qty: float          # unidades netas que entran (buy) o salen (sell) del libro
    cash_delta: float   # EUR netos: negativo en compras, positivo en ventas
    fee: float          # comisión estimada en EUR
    price: float
    order_id: str = ""


MAX_SPREAD = 0.01  # un bid/ask a más del 1 % del último precio no es creíble: se ignora


def simulate_fill(order: Order, price: float, cfg: Config) -> Fill:
    """Fill simulado con comisión y slippage. Lo usan el backtest y el modo paper."""
    if order.side == "buy":
        px = price * (1 + cfg.slippage)
        fee = order.value * cfg.fee_rate
        return Fill(order.symbol, "buy", (order.value - fee) / px, -order.value, fee, px)
    px = price * (1 - cfg.slippage)
    gross = order.qty * px
    fee = gross * cfg.fee_rate
    return Fill(order.symbol, "sell", order.qty, gross - fee, fee, px)


class PaperBroker:
    """Precios reales del exchange, ejecución simulada. No necesita claves."""

    def __init__(self, cfg: Config, exchange=None):
        self.cfg = cfg
        self._ex = exchange

    @property
    def ex(self):
        if self._ex is None:
            from .data import public_exchange

            self._ex = public_exchange(self.cfg)
        return self._ex

    def price(self, symbol: str) -> float:
        return float(self.ex.fetch_ticker(symbol)["last"])

    def free(self) -> dict | None:
        return None  # no hay cuenta real que cuadrar

    def execute(self, order: Order, ref_price: float) -> Fill | None:
        """Compra al precio al que de verdad se vende (ask) y vende al que de verdad se compra (bid).

        Así el modo paper paga el spread real del momento en vez de un slippage supuesto.
        Si el exchange no da esos precios, o dan algo raro, se usa el slippage de la configuración.
        """
        try:
            ticker = self.ex.fetch_ticker(order.symbol)
            px = float(ticker.get("ask" if order.side == "buy" else "bid") or 0)
        except Exception:
            px = 0.0
        if px <= 0 or abs(px / ref_price - 1) > MAX_SPREAD:
            return simulate_fill(order, ref_price, self.cfg)
        return simulate_fill(order, px, replace(self.cfg, slippage=0.0))


class LiveBroker:
    """Órdenes a mercado reales. La clave API debe tener SOLO permiso de trading."""

    def __init__(self, cfg: Config, exchange=None):
        self.cfg = cfg
        if exchange is None:
            import ccxt

            key, secret = os.environ.get("API_KEY"), os.environ.get("API_SECRET")
            if not key or not secret:
                raise RuntimeError("Modo live sin API_KEY / API_SECRET en .env")
            exchange = getattr(ccxt, cfg.exchange)(
                {"apiKey": key, "secret": secret, "enableRateLimit": True}
            )
            exchange.load_markets()
        self.ex = exchange

    def price(self, symbol: str) -> float:
        return float(self.ex.fetch_ticker(symbol)["last"])

    def free(self) -> dict:
        return self.ex.fetch_balance()["free"]

    def _min_cost(self, symbol: str) -> float:
        limits = self.ex.market(symbol).get("limits") or {}
        return float((limits.get("cost") or {}).get("min") or 0.0)

    def _settle(self, raw: dict, symbol: str) -> dict:
        """Si la respuesta no trae el fill completo, consulta la orden un par de veces."""
        for _ in range(3):
            if raw.get("status") == "closed" and float(raw.get("filled") or 0) > 0:
                return raw
            time.sleep(1.5)
            raw = self.ex.fetch_order(raw["id"], symbol)
        return raw

    def execute(self, order: Order, ref_price: float) -> Fill | None:
        symbol = order.symbol
        base, quote = symbol.split("/")
        min_cost = self._min_cost(symbol)

        if order.side == "buy":
            cost = math.floor(order.value * 100) / 100
            if cost < max(min_cost, 0.01):
                return None
            raw = self.ex.create_market_buy_order_with_cost(symbol, cost)
        else:
            amount = float(self.ex.amount_to_precision(symbol, order.qty))
            if amount <= 0 or amount * ref_price < min_cost:
                return None  # polvo: por debajo del mínimo del exchange
            raw = self.ex.create_market_sell_order(symbol, amount)

        raw = self._settle(raw, symbol)
        filled = float(raw.get("filled") or 0)
        cost = float(raw.get("cost") or 0)
        if filled <= 0 or cost <= 0:
            raise RuntimeError(f"Orden {raw.get('id')} sin ejecución confirmada: {raw.get('status')}")
        avg = cost / filled

        fees = raw.get("fees") or ([raw["fee"]] if raw.get("fee") else [])
        fee_base = sum(float(f.get("cost") or 0) for f in fees if f.get("currency") == base)
        fee_quote = sum(float(f.get("cost") or 0) for f in fees if f.get("currency") == quote)
        if not fees:
            # Sin detalle: se asume la comisión estándar sobre el activo recibido.
            if order.side == "buy":
                fee_base = filled * self.cfg.fee_rate
            else:
                fee_quote = cost * self.cfg.fee_rate
        fee_eur = fee_base * avg + fee_quote

        if order.side == "buy":
            return Fill(symbol, "buy", filled - fee_base, -(cost + fee_quote), fee_eur, avg, str(raw.get("id", "")))
        return Fill(symbol, "sell", filled, cost - fee_quote, fee_eur, avg, str(raw.get("id", "")))
