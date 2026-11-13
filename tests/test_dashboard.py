import json
from datetime import timedelta, timezone

import pytest

from bot import dashboard as db
from bot import state as store
from bot.config import Config
from bot.strategy import latest_target
from conftest import make_prices


class Book:
    def __init__(self, prices):
        self.prices = prices

    def fetch_ticker(self, symbol):
        last = self.prices[symbol]
        return {"last": last, "bid": last * 0.9999, "ask": last * 1.0001}


@pytest.fixture
def world(tmp_path):
    cfg = Config(root=tmp_path)
    opens, closes = make_prices()
    now = (closes.index[-1] + timedelta(days=1, hours=9)).to_pydatetime().replace(tzinfo=timezone.utc)
    calls = []

    def fetch(c, since, refresh=False):
        calls.append(refresh)
        return opens, closes

    ex = Book({s: float(closes[s].iloc[-1]) * 1.01 for s in cfg.symbols})
    return cfg, closes, now, fetch, ex, calls


def test_snapshot_is_plain_json_and_tells_the_whole_story(world):
    cfg, closes, now, fetch, ex, calls = world
    snap = db.build(cfg, ex=ex, fetch=fetch, now=now)
    json.dumps(snap, allow_nan=False)  # sin NaN ni tipos raros
    assert calls == [False]            # la caché estaba al día: no se vuelve a descargar

    acc = snap["cuenta"]
    assert acc["modo"] == "paper" and acc["equity"] == 100.0 and acc["caja"] == 100.0
    assert acc["al_dia"] is False and acc["parado"] is False  # aún no ha procesado ninguna vela

    target = latest_target(closes, cfg)
    for s in cfg.symbols:
        m = snap["mercado"][s]
        assert m["peso"] == pytest.approx(target[s], abs=1e-4)
        assert m["votos"] == sum(r["dentro"] for r in m["reglas"]) == m["votos_dia"][-1]
        assert len(m["fechas"]) == len(m["cierres"]) == len(m["peso_dia"]) == db.CHART_DAYS
        assert set(m["medias"]) == {"50", "100", "150", "200"}
        assert m["spread"] == pytest.approx(0.0002, rel=1e-3)
        assert m["cambio_desde_cierre"] == pytest.approx(0.01)

    bt = snap["backtest"]
    assert len(bt["fechas"]) == len(bt["estrategia"]) == len(bt["comprar"])
    assert len(bt["reglas"]) == 12 and isinstance(bt["pasa"], bool)
    assert bt["estrategia"][0] == cfg.budget and 0 <= bt["suerte"]["negativos"] <= 1
    # Lo que haría ahora: con todo en caja, solo puede comprar, y nunca más de lo que hay
    assert all(o["lado"] == "buy" for o in snap["si_cerrase_ahora"])
    assert sum(o["euros"] for o in snap["si_cerrase_ahora"]) <= acc["caja"]


def test_stale_cache_is_downloaded_again(world):
    cfg, closes, now, fetch, ex, calls = world
    db.build(cfg, ex=ex, fetch=fetch, now=now + timedelta(days=3))
    assert calls == [False, True]


def test_backtest_is_computed_once_per_candle(world, monkeypatch):
    cfg, closes, now, fetch, ex, calls = world
    first = db.build(cfg, ex=ex, fetch=fetch, now=now)["backtest"]
    monkeypatch.setattr(db, "analyze", lambda *a, **k: pytest.fail("debía salir de la caché"))
    assert db.build(cfg, ex=ex, fetch=fetch, now=now)["backtest"] == first
    # Otra configuración no puede reutilizar esa caché
    monkeypatch.undo()
    other = db.build(Config(root=cfg.root, target_vol=0.15), ex=ex, fetch=fetch, now=now)["backtest"]
    assert other["clave"] != first["clave"]


def test_snapshot_never_touches_the_ledger_and_shows_a_halt(world):
    cfg, closes, now, fetch, ex, calls = world
    st = store.load(cfg)
    st.positions["BTC/EUR"], st.cash = 0.1, 60.0
    st.halted, st.halt_reason, st.last_candle = True, "Kill switch", closes.index[-1].strftime("%Y-%m-%d")
    store.save(cfg, st)
    before = (cfg.state_dir / "state.json").read_text()

    snap = db.build(cfg, ex=ex, fetch=fetch, now=now)
    assert (cfg.state_dir / "state.json").read_text() == before
    assert sorted(p.name for p in cfg.state_dir.iterdir()) == ["state.json"]
    acc = snap["cuenta"]
    assert acc["parado"] and acc["motivo_paro"] == "Kill switch" and acc["al_dia"]
    assert acc["posiciones"]["BTC/EUR"]["valor"] == pytest.approx(0.1 * ex.prices["BTC/EUR"], abs=0.01)
    assert acc["equity"] == pytest.approx(60 + 0.1 * ex.prices["BTC/EUR"], abs=0.01)
    assert snap["si_cerrase_ahora"] == []  # parado: no haría nada


def test_cli_writes_and_prints_the_same_json(world, monkeypatch, capsys):
    cfg, closes, now, fetch, ex, calls = world
    monkeypatch.setattr(db, "from_env", lambda: cfg)
    monkeypatch.setattr(db, "build", lambda c: {"version": db.VERSION, "ok": True})
    db.main()
    assert json.loads(capsys.readouterr().out) == json.loads((cfg.root / "reports" / "dashboard.json").read_text())
