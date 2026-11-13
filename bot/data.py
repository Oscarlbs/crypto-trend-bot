"""Velas diarias vía ccxt (endpoints públicos, no hacen falta claves)."""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from .config import Config

DAY_MS = 86_400_000
COLS = ["open", "high", "low", "close", "volume"]


def public_exchange(cfg: Config):
    import ccxt

    return getattr(ccxt, cfg.exchange)({"enableRateLimit": True})


def _to_frame(rows: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", *COLS])
    df = df.drop_duplicates("ts").sort_values("ts")
    # Solo velas cerradas: la de hoy sigue abierta y metería look-ahead.
    df = df[df["ts"] + DAY_MS <= int(time.time() * 1000)]
    df.index = _dates(pd.to_datetime(df["ts"], unit="ms").dt.normalize())
    return df[COLS].astype(float)


def _dates(values) -> pd.DatetimeIndex:
    """Índice de fechas con la misma resolución venga de la API o de la caché."""
    return pd.DatetimeIndex(values, name="date").as_unit("ns")


def fetch_daily(ex, symbol: str, since: str | None = None, limit: int | None = None) -> pd.DataFrame:
    """Histórico paginado desde `since` (YYYY-MM-DD), o las últimas `limit` velas."""
    if since is None:
        return _to_frame(ex.fetch_ohlcv(symbol, "1d", limit=limit or 500))
    cursor = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)
    rows: list[list] = []
    while True:
        batch = ex.fetch_ohlcv(symbol, "1d", since=cursor, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        nxt = batch[-1][0] + DAY_MS
        if nxt <= cursor or len(batch) < 2:
            break
        cursor = nxt
        if cursor > int(time.time() * 1000):
            break
    return _to_frame(rows)


def history(cfg: Config, since: str, cache_dir: Path | None = None, refresh: bool = False):
    """(opens, closes) alineados por fecha para todos los símbolos. Cachea en CSV."""
    cache_dir = cache_dir or cfg.root / "data"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ex = None
    frames = {}
    for symbol in cfg.symbols:
        path = cache_dir / f"{symbol.replace('/', '-')}_1d_{since}.csv"
        if path.exists() and not refresh:
            df = pd.read_csv(path, index_col="date", parse_dates=True)
            df.index = _dates(df.index)
        else:
            ex = ex or public_exchange(cfg)
            df = fetch_daily(ex, symbol, since=since)
            df.to_csv(path)
        frames[symbol] = df
    opens = pd.concat({s: f["open"] for s, f in frames.items()}, axis=1).dropna()
    closes = pd.concat({s: f["close"] for s, f in frames.items()}, axis=1).dropna()
    return opens, closes


def recent_closes(cfg: Config, ex=None) -> pd.DataFrame:
    """Cierres recientes (solo velas cerradas) suficientes para calcular la señal."""
    ex = ex or public_exchange(cfg)
    n = cfg.warmup + 60
    cols = {s: fetch_daily(ex, s, limit=n)["close"] for s in cfg.symbols}
    return pd.concat(cols, axis=1).dropna()
