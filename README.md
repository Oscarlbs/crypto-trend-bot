# crypto-trend-bot

A trend-following bot that trades BTC/EUR and ETH/EUR on Binance spot once a day. Long only, no leverage. It runs in Docker on a small server, sends a daily summary to Telegram, and has a little macOS app to check how it's doing.

Not financial advice. It can lose money; the kill switch limits the loss, it doesn't prevent it.

## How it decides

Once a day, after the daily candle closes:

1. **Signal**: 12 rules vote for each asset. Each rule is a moving average (50, 100, 150 or 200 days) paired with a momentum window (20, 30 or 60 days), and it votes "in" when the close is above the average and the return over the window is positive. The position size follows the share of votes.
2. **Sizing**: 25% annual volatility target per asset, capped at 50% per asset, scaled by the votes. The rest stays in EUR.
3. **Band**: it only trades if the target weight is more than 10 points away from the current one. If no rule votes "in", it exits straight away.
4. **Kill switch**: if equity drops below 75% of the budget, it sells everything and stops until I turn it back on.

The parameters are in `bot/config.py` and were not tuned on the backtest. Twelve rules voting instead of one is there so the result doesn't depend on picking a lucky pair of numbers.

## Backtest

Real Binance EUR data from July 2020 to November 2026:

| | Bot | Buy and hold |
|---|---:|---:|
| Sharpe | 1.33 | 0.84 |
| Max drawdown | −31.9% | −74.8% |

I also tested it on data it had never seen: BTC and ETH in USDT from 2018 to 2020 (including the 80% crash), seven other coins one by one, fees up to 8 times higher and delayed execution. The 12-rule vote doesn't clearly beat the single-rule version, but it never does worse on any dataset, pays fewer fees and copes better with high costs. Running a day late costs about 0.1 of Sharpe, so it matters that it runs every night.

## How it's built

- `strategy.py` is a pure function from prices to target weights. Order planning, risk and execution live in separate modules.
- Paper and live brokers share one interface. Paper mode uses real bid and ask prices with simulated fills.
- The bot keeps its own ledger and only trades its own budget, even if the account holds more. If the ledger doesn't fit the free balance it aborts, and if a live order fails halfway it stops and sends an alert.
- The container has no open ports, runs as a non-root user on a read-only filesystem with all capabilities dropped and memory/CPU limits.
- The macOS app (Swift + WebKit) is read only. It reads the data over SSH with two fixed commands, so nothing new runs or listens on the server.
- Tests cover the strategy, risk, backtest, scheduler, notifications and the panel, including quoting of the SSH commands.

```
bot/config.py      parameters and .env loading
bot/data.py        daily candles (ccxt, public endpoints)
bot/strategy.py    prices -> target weights
bot/risk.py        order planning, band, kill switch
bot/broker.py      PaperBroker and LiveBroker (ccxt)
bot/state.py       ledger (JSON) and trade logs (CSV)
bot/run.py         daily job, safe to re-run
bot/scheduler.py   hourly loop inside the container
bot/backtest.py    backtest, comparison and sensitivity
bot/notify.py      Telegram alerts
bot/dashboard.py   data snapshot for the panel
panel/             macOS app and browser version of the panel
```

## Running it

```bash
cp .env.example .env                                          # paper mode by default
docker compose run --rm trendbot python -m bot.backtest       # backtest with real Binance data
docker compose run --rm trendbot python -m bot.run --dry-run  # what it would do today
docker compose up -d
```

Local tests:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

The full guide in Spanish (paper phase, Telegram setup and going live) is in [`LEEME.md`](LEEME.md).
