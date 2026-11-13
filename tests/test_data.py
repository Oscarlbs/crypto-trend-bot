import time
from dataclasses import replace

import pandas as pd

from bot import data
from bot.config import Config

DAY = data.DAY_MS


class FakeOHLCV:
    """Sirve velas diarias desde 2020-01-01 hasta HOY inclusive (la de hoy, aún abierta)."""

    def __init__(self):
        self.start = int(pd.Timestamp("2020-01-01", tz="UTC").timestamp() * 1000)
        self.today = int(time.time() * 1000) // DAY * DAY
        self.calls = 0

    def _candle(self, ts, k):
        px = 100.0 + (ts - self.start) / DAY * (1 + k)
        return [ts, px, px + 1, px - 1, px + 0.5, 10.0]

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=500):
        self.calls += 1
        k = 0 if symbol.startswith("BTC") else 1
        n_total = (self.today - self.start) // DAY + 1
        if since is None:
            first = max(0, n_total - limit)
        else:
            first = max(0, -(-(since - self.start) // DAY))
        return [self._candle(self.start + i * DAY, k) for i in range(first, min(first + limit, n_total))]


def test_history_paginates_and_drops_open_candle(tmp_path, monkeypatch):
    ex = FakeOHLCV()
    monkeypatch.setattr(data, "public_exchange", lambda cfg: ex)
    cfg = replace(Config(), root=tmp_path)

    opens, closes = data.history(cfg, "2020-01-01")
    yesterday = pd.Timestamp(ex.today - DAY, unit="ms")
    assert closes.index[0] == pd.Timestamp("2020-01-01")
    assert closes.index[-1] == yesterday            # la vela de hoy no entra
    assert closes.index.is_unique and closes.index.is_monotonic_increasing
    assert len(closes) == (ex.today - ex.start) // DAY > 1000   # hubo paginación
    assert list(closes.columns) == list(cfg.symbols) == list(opens.columns)

    calls = ex.calls
    opens2, closes2 = data.history(cfg, "2020-01-01")           # segunda vez: caché
    assert ex.calls == calls
    pd.testing.assert_frame_equal(closes, closes2, check_freq=False)


def test_recent_closes_has_enough_for_signal(tmp_path):
    cfg = replace(Config(), root=tmp_path)
    closes = data.recent_closes(cfg, ex=FakeOHLCV())
    assert len(closes) >= cfg.warmup
    assert closes.index[-1] == pd.Timestamp(int(time.time() * 1000) // DAY * DAY - DAY, unit="ms")
