from datetime import datetime, timedelta, timezone

import pytest

from bot import state as store
from bot.broker import LiveBroker, PaperBroker
from bot.config import Config
from bot.run import run
from conftest import FakeExchange, FakeTicker, make_prices


def setup(tmp_path, mode="paper", drifts=(0.004, -0.004, 0.003)):
    cfg = Config(root=tmp_path, mode=mode)
    _, closes = make_prices(drifts=drifts)
    now = (closes.index[-1] + timedelta(days=1)).to_pydatetime().replace(tzinfo=timezone.utc)
    last = {s: float(closes[s].iloc[-1]) for s in cfg.symbols}
    return cfg, closes, now, last


def test_paper_run_is_idempotent(tmp_path):
    cfg, closes, now, last = setup(tmp_path)
    msgs = []
    kw = dict(fetch=lambda c: closes, broker=PaperBroker(cfg, FakeTicker(last)), notifier=msgs.append, now=now)

    assert run(cfg, **kw) == "ok"
    st = store.load(cfg)
    assert st.cash < cfg.budget and any(q > 0 for q in st.positions.values())
    assert (cfg.state_dir / "trades.csv").exists() and len(msgs) == 1

    assert run(cfg, **kw) == "skipped"
    assert store.load(cfg) == st and len(msgs) == 1


def test_dry_run_changes_nothing(tmp_path):
    cfg, closes, now, last = setup(tmp_path)
    out = run(cfg, dry_run=True, fetch=lambda c: closes, broker=PaperBroker(cfg, FakeTicker(last)),
              notifier=lambda t: None, now=now)
    assert out == "dry-run" and not cfg.state_dir.exists()


def test_stale_data_aborts(tmp_path):
    cfg, closes, now, last = setup(tmp_path)
    with pytest.raises(RuntimeError, match="desfasados"):
        run(cfg, fetch=lambda c: closes, broker=PaperBroker(cfg, FakeTicker(last)),
            notifier=lambda t: None, now=now + timedelta(days=4))


def test_kill_switch_liquidates_and_halts(tmp_path):
    cfg, closes, now, last = setup(tmp_path)
    kw = dict(fetch=lambda c: closes, notifier=lambda t: None, now=now)
    run(cfg, broker=PaperBroker(cfg, FakeTicker(last)), **kw)

    crashed = {s: p * 0.3 for s, p in last.items()}  # desplome: equity por debajo de 75
    assert run(cfg, force=True, broker=PaperBroker(cfg, FakeTicker(crashed)), **kw) == "killed"
    st = store.load(cfg)
    assert st.halted and all(q == 0 for q in st.positions.values())
    assert run(cfg, force=True, broker=PaperBroker(cfg, FakeTicker(last)), **kw) == "halted"


def test_live_ledger_matches_exchange(tmp_path):
    cfg, closes, now, last = setup(tmp_path, mode="live")
    ex = FakeExchange(last, eur=250.0)  # la cuenta tiene más dinero que el presupuesto del bot
    kw = dict(fetch=lambda c: closes, broker=LiveBroker(cfg, ex), notifier=lambda t: None, now=now)

    assert run(cfg, **kw) == "ok"
    st = store.load(cfg)
    assert ex.orders and all(side == "buy" for side, *_ in ex.orders)
    assert st.positions["BTC/EUR"] == pytest.approx(ex.bal["BTC"])
    assert st.positions["ETH/EUR"] == pytest.approx(ex.bal["ETH"])
    assert st.cash == pytest.approx(ex.bal["EUR"] - 150.0)  # solo toca sus 100 EUR
    assert st.cash >= 0

    # Mercado bajista -> objetivo 0 -> vende; el libro sigue cuadrando con la cuenta.
    _, bear = make_prices(drifts=(0.004, 0.003, -0.006))
    bear.index = closes.index
    kw["fetch"] = lambda c: bear
    assert run(cfg, force=True, **kw) == "ok"
    st = store.load(cfg)
    assert any(side == "sell" for side, *_ in ex.orders)
    assert st.cash == pytest.approx(ex.bal["EUR"] - 150.0)
    for s in cfg.symbols:  # como mucho queda polvo por redondeo de precisión
        assert st.positions[s] * last[s] < 0.05
        assert st.positions[s] <= ex.bal[s.split("/")[0]] + 1e-12


def test_live_refuses_when_account_lacks_funds(tmp_path):
    cfg, closes, now, last = setup(tmp_path, mode="live")
    ex = FakeExchange(last, eur=50.0)
    with pytest.raises(RuntimeError, match="libro"):
        run(cfg, fetch=lambda c: closes, broker=LiveBroker(cfg, ex), notifier=lambda t: None, now=now)
    assert ex.orders == []


def test_live_execution_error_halts(tmp_path):
    cfg, closes, now, last = setup(tmp_path, mode="live")
    msgs = []
    ex = FakeExchange(last, eur=100.0, fail_on="buy")
    with pytest.raises(ConnectionError):
        run(cfg, fetch=lambda c: closes, broker=LiveBroker(cfg, ex), notifier=msgs.append, now=now)
    assert store.load(cfg).halted and "PARADO" in msgs[0]


def test_network_error_before_orders_retries_without_halting(tmp_path):
    cfg, closes, now, last = setup(tmp_path, mode="live")
    ex = FakeExchange(last, eur=100.0)

    def no_network(c):
        raise ConnectionError("sin wifi")

    with pytest.raises(ConnectionError):
        run(cfg, fetch=no_network, broker=LiveBroker(cfg, ex), notifier=lambda t: None, now=now)
    assert not cfg.state_dir.exists() and ex.orders == []   # ni estado ni órdenes

    # Vuelve la red: la siguiente pasada procesa la misma vela con normalidad.
    assert run(cfg, fetch=lambda c: closes, broker=LiveBroker(cfg, ex), notifier=lambda t: None, now=now) == "ok"
    assert not store.load(cfg).halted and ex.orders


def test_halt_alert_repeats_until_delivered(tmp_path):
    cfg, closes, now, last = setup(tmp_path, mode="live")
    ex = FakeExchange(last, eur=100.0, fail_on="buy")
    with pytest.raises(ConnectionError):
        run(cfg, fetch=lambda c: closes, broker=LiveBroker(cfg, ex), notifier=lambda t: False, now=now)

    kw = dict(fetch=lambda c: closes, broker=LiveBroker(cfg, ex), now=now)
    lost, got = [], []
    # Sin red el aviso no llega: no se da por enviado y se reintenta.
    assert run(cfg, notifier=lambda t: lost.append(t) or False, **kw) == "halted"
    assert run(cfg, notifier=lambda t: lost.append(t) or False, **kw) == "halted"
    assert len(lost) == 2 and store.load(cfg).halt_alerted is None
    # Con red llega una vez y no se repite ese día; al día siguiente, otra vez.
    assert run(cfg, notifier=got.append, **kw) == "halted"
    assert run(cfg, notifier=got.append, **kw) == "halted"
    assert len(got) == 1 and "sigue PARADO" in got[0]
    kw["now"] = now + timedelta(days=1)
    assert run(cfg, notifier=got.append, **kw) == "halted"
    assert len(got) == 2


def test_paper_pays_the_real_spread_when_the_exchange_gives_it(tmp_path):
    from bot.risk import Order

    class Book(FakeTicker):
        def fetch_ticker(self, symbol):
            last = self.prices[symbol]
            return {"last": last, "bid": last * 0.999, "ask": last * 1.001}

    cfg = Config(root=tmp_path)
    px = {"BTC/EUR": 50_000.0}
    buy = PaperBroker(cfg, Book(px)).execute(Order("BTC/EUR", "buy", value=50.0), 50_000.0)
    assert buy.price == pytest.approx(50_050.0)                      # compra al ask, sin slippage añadido
    assert buy.qty == pytest.approx(50.0 * (1 - cfg.fee_rate) / 50_050.0)
    sell = PaperBroker(cfg, Book(px)).execute(Order("BTC/EUR", "sell", value=50.0, qty=0.001), 50_000.0)
    assert sell.price == pytest.approx(49_950.0)                     # vende al bid

    # Sin bid/ask, o con uno disparatado, vuelve al slippage supuesto
    plain = PaperBroker(cfg, FakeTicker(px)).execute(Order("BTC/EUR", "buy", value=50.0), 50_000.0)
    assert plain.price == pytest.approx(50_000.0 * (1 + cfg.slippage))

    class Broken(FakeTicker):
        def fetch_ticker(self, symbol):
            return {"last": 50_000.0, "bid": 1.0, "ask": 90_000.0}

    odd = PaperBroker(cfg, Broken(px)).execute(Order("BTC/EUR", "buy", value=50.0), 50_000.0)
    assert odd.price == pytest.approx(50_000.0 * (1 + cfg.slippage))


def test_the_demo_money_never_leaks_into_live(monkeypatch, tmp_path):
    from bot import config

    monkeypatch.setattr(config, "load_env", lambda path=None: None)
    monkeypatch.setenv("BOT_BUDGET", "100")
    monkeypatch.setenv("BOT_MODE", "paper")
    assert config.from_env().budget == 10_000  # la prueba ignora BOT_BUDGET
    monkeypatch.setenv("BOT_MODE", "live")
    assert config.from_env().budget == 100     # en real manda BOT_BUDGET
    monkeypatch.delenv("BOT_BUDGET")
    assert config.from_env().budget == 100     # y sin él, el valor prudente
