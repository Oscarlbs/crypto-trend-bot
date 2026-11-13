from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from bot.backtest import buy_and_hold, metrics, report, run_backtest
from bot.config import Config

CFG = Config()


def test_flat_prices_never_trade():
    idx = pd.date_range("2023-01-01", periods=400, freq="D")
    flat = pd.DataFrame({s: np.full(400, 100.0) for s in CFG.symbols}, index=idx)
    res = run_backtest(flat, flat, CFG)
    assert res["trades"] == 0 and res["fees"] == 0
    assert (res["equity"] == CFG.budget).all()


def test_costs_reduce_equity(prices):
    opens, closes = prices
    with_costs = run_backtest(opens, closes, CFG)
    free = run_backtest(opens, closes, replace(CFG, fee_rate=0.0, slippage=0.0))
    assert with_costs["trades"] > 0 and with_costs["fees"] > 0
    assert free["equity"].iloc[-1] > with_costs["equity"].iloc[-1]
    assert (with_costs["equity"] > 0).all()


def test_buy_and_hold_matches_formula():
    idx = pd.date_range("2023-01-01", periods=200, freq="D")
    px = pd.DataFrame({s: np.linspace(100, 300, 200) for s in CFG.symbols}, index=idx)
    start = 10
    bh = buy_and_hold(px, px, CFG, {"BTC/EUR": 1.0}, start)
    entry = px.iloc[start + 1, 0] * (1 + CFG.slippage)
    expected = CFG.budget * (1 - CFG.fee_rate) / entry * px.iloc[-1, 0]
    assert bh.iloc[-1] == pytest.approx(expected)
    assert bh.iloc[0] == CFG.budget


def test_trend_filter_cuts_drawdown(prices):
    opens, closes = prices
    start = CFG.warmup
    strat = metrics(run_backtest(opens, closes, CFG)["equity"])
    hold = metrics(buy_and_hold(opens, closes, CFG, {s: 0.5 for s in CFG.symbols}, start))
    assert abs(strat["maxdd"]) < abs(hold["maxdd"])  # el tramo bajista lo pasa en caja


def test_report_runs(prices):
    opens, closes = prices
    text, curves, passed = report(opens, closes, CFG)
    assert "VEREDICTO" in text and isinstance(passed, bool)
    assert list(curves.columns)[:2] == ["estrategia", "comprar_y_mantener"]
    assert not curves.isna().any().any()
