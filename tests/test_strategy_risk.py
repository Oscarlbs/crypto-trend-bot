import numpy as np
import pandas as pd

from bot.config import Config
from bot.risk import plan_orders, should_kill
from bot.strategy import explain, latest_target, target_weights, trend_votes

CFG = Config()
PX = {"BTC/EUR": 100.0, "ETH/EUR": 10.0}


def test_no_lookahead(prices):
    _, closes = prices
    full = target_weights(closes, CFG)
    for t in (250, 480, 700):
        trunc = target_weights(closes.iloc[: t + 1], CFG)
        np.testing.assert_allclose(full.iloc[: t + 1].values, trunc.values)


def test_weight_bounds(prices):
    _, closes = prices
    w = target_weights(closes, CFG)
    assert (w.values >= 0).all()
    assert (w.values <= CFG.max_weight + 1e-12).all()
    assert (w.sum(axis=1) <= 1 + 1e-12).all()
    assert (w.iloc[: min(CFG.sma_grid) - 1].values == 0).all()  # sin datos, fuera
    assert (w.values > 0).any()


def test_downtrend_is_flat():
    idx = pd.date_range("2024-01-01", periods=300, freq="D")
    closes = pd.DataFrame({s: 100 * 0.995 ** np.arange(300) for s in CFG.symbols}, index=idx)
    assert target_weights(closes, CFG).values.sum() == 0
    assert latest_target(closes, CFG) == {s: 0.0 for s in CFG.symbols}


def test_entry_from_cash():
    orders = plan_orders({"BTC/EUR": 0.45, "ETH/EUR": 0.33}, {}, PX, 100.0, CFG)
    assert [(o.side, round(o.value, 2)) for o in orders] == [("buy", 45.0), ("buy", 33.0)]


def test_band_blocks_small_changes():
    pos = {"BTC/EUR": 0.40, "ETH/EUR": 0.0}  # 40 EUR en BTC, 60 en caja
    assert plan_orders({"BTC/EUR": 0.45, "ETH/EUR": 0.0}, pos, PX, 60.0, CFG) == []


def test_full_exit_ignores_band_and_sells_first():
    pos = {"BTC/EUR": 0.40, "ETH/EUR": 0.0}
    orders = plan_orders({"BTC/EUR": 0.0, "ETH/EUR": 0.45}, pos, PX, 60.0, CFG)
    assert [o.side for o in orders] == ["sell", "buy"]
    assert orders[0].qty == 0.40


def test_buys_capped_by_cash():
    orders = plan_orders({"BTC/EUR": 0.5, "ETH/EUR": 0.5}, {}, PX, 100.0, CFG)
    assert sum(o.value for o in orders) <= 100.0 * 0.998 + 1e-9


def test_kill_threshold():
    assert should_kill(74.99, CFG) and not should_kill(75.0, CFG)


def test_votes_are_the_share_of_rules_in_trend(prices):
    _, closes = prices
    votes = trend_votes(closes, CFG)
    assert ((votes.values >= 0) & (votes.values <= 1)).all()
    steps = np.round(votes.values * len(CFG.rules), 9)
    assert (steps == steps.round()).all()  # siempre un número entero de reglas
    up = pd.DataFrame({s: 100 * 1.01 ** np.arange(300) for s in CFG.symbols}, index=closes.index[:300])
    assert (trend_votes(up, CFG).iloc[-1] == 1.0).all()  # subida limpia: todas a favor


def test_one_rule_equals_the_classic_filter(prices):
    _, closes = prices
    one = Config(sma_grid=(100,), mom_grid=(30,))
    px = closes["BTC/EUR"]
    classic = ((px > px.rolling(100).mean()) & (px / px.shift(30) - 1 > 0)).astype(float)
    np.testing.assert_array_equal(trend_votes(closes, one)["BTC/EUR"].values, classic.values)
    assert len(CFG.rules) == 12 and CFG.warmup == 201


def test_explain_matches_the_target(prices):
    _, closes = prices
    why = explain(closes, CFG)
    target = latest_target(closes, CFG)
    votes = trend_votes(closes, CFG).iloc[-1]
    for s in CFG.symbols:
        assert why[s]["peso"] == target[s]
        assert why[s]["votos"] == round(votes[s] * 12) and why[s]["total"] == 12
        assert why[s]["votos"] == sum(r["dentro"] for r in why[s]["reglas"])
        assert all(r["dentro"] == (r["sobre_media"] and r["sube"]) for r in why[s]["reglas"])
        assert 0 <= why[s]["peso"] <= why[s]["tamano_completo"] <= CFG.max_weight
