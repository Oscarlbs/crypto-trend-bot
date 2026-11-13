<div align="center">

# 📈 Crypto Trend Bot

**A daily trend-following bot for BTC/EUR and ETH/EUR on Binance spot. Long only, no leverage.**
It is backtested, risk-managed, containerised, and watched from a native macOS dashboard.

![Python](https://img.shields.io/badge/Python_3.12-3776AB?logo=python&logoColor=white)
![pandas](https://img.shields.io/badge/pandas-150458?logo=pandas&logoColor=white)
![ccxt](https://img.shields.io/badge/ccxt-Binance-F0B90B?logo=binance&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![Swift](https://img.shields.io/badge/Swift-macOS-F05138?logo=swift&logoColor=white)
![Tests](https://img.shields.io/badge/tests-52-brightgreen)

</div>

> ⚠️ This is a personal engineering project, not financial advice. It can lose money: the kill switch limits losses but does not prevent them.

## How it decides

Once a day, after the daily candle closes:

1. **Signal.** For each asset, **12 rules vote**. Each rule pairs a moving average (50/100/150/200 days) with a momentum window (20/30/60 days). A rule votes *in* when the close is above its average **and** the return over its window is positive. The position is proportional to the votes in favour.
2. **Sizing.** It targets 25% annualised volatility per asset, with a 50% cap per asset, scaled by the vote fraction. The rest stays in EUR.
3. **Band.** It only trades when the target weight drifts more than 10 points from the current one. If no rule votes *in*, it exits fully at once.
4. **Kill switch.** If equity falls below 75% of the budget, it sells everything and halts until it is manually re-enabled.

Parameters live in `bot/config.py` and are **not optimised on the backtest**. Using 12 votes instead of one rule removes the dependence on a lucky parameter pair.

## Results

**Backtest** on real Binance EUR data, 22 Jul 2020 → 10 Nov 2026:

| | Trend bot | Buy & hold |
|---|---:|---:|
| Sharpe ratio | **1.33** | 0.84 |
| Max drawdown | **−31.9%** | −74.8% |

**Out-of-sample robustness** (data the strategy never saw: BTC/ETH in USDT 2018–2020, including the −80% crash, 7 other coins one by one, costs up to 8× higher, and delayed execution):

- The single-rule baseline already scored a Sharpe of 0.83 against 0.12 for buy & hold in 2018–2020, and beat buy & hold on 6 of 9 coins.
- The 12-rule vote never loses on any dataset, slightly reduces the worst drawdown, pays fewer fees and handles high costs better.
- Rejected because they failed out of sample or were hindsight picks: a third coin, a different volatility window, a different band or a different volatility target.
- Running one day late costs about 0.1 Sharpe and raises the worst drawdown from 34% to 43%. Running every night matters.

## Engineering

- **Pure strategy function** (`prices → target weights`), separated from order planning, risk and execution.
- **Paper and live brokers** behind one interface. Paper mode uses real bid/ask prices with simulated fills.
- **Its own ledger:** the bot only trades its own budget, even if the account holds more. Before trading it checks that its ledger fits the free balance, and aborts if it doesn't. If a live order fails halfway, it stops and alerts.
- **Idempotent daily job** with an hourly scheduler that tolerates errors and shuts down cleanly mid-order.
- **Hardened container:** no exposed ports, non-root user, read-only filesystem, `cap_drop: ALL`, `no-new-privileges`, and memory, CPU and PID limits.
- **Telegram alerts:** a daily heartbeat with equity, target and trades, plus errors and kill-switch events.
- **BananaTrading**, a native macOS app (Swift + WebKit). It shows equity, today's votes per rule, the paper-trading journal and the backtest. It is read-only and reaches the server over SSH with two fixed commands, so no port is opened on the server.
- **52 tests** covering strategy, risk, backtest, scheduler, notifications, dashboard and panel, including shell-injection safety for the SSH commands.

```
bot/config.py      parameters and .env loading
bot/data.py        daily candles (ccxt, public endpoints)
bot/strategy.py    pure function: prices -> target weights
bot/risk.py        order planning, band, kill switch
bot/broker.py      PaperBroker (simulated) and LiveBroker (ccxt)
bot/state.py       bot ledger (JSON) and trade logs (CSV)
bot/run.py         idempotent daily job
bot/scheduler.py   container loop: hourly, error-tolerant, clean shutdown
bot/backtest.py    backtest, comparison, sensitivity, verdict
bot/notify.py      Telegram alerts (optional)
bot/dashboard.py   read-only data snapshot for the panel
panel/             BananaTrading macOS app (Swift) and browser version (server.py)
```

## Run it

```bash
cp .env.example .env                                         # BOT_MODE=paper by default
docker compose run --rm trendbot python -m bot.backtest      # backtest on real Binance data
docker compose run --rm trendbot python -m bot.run --dry-run # what it would do today, without trading
docker compose up -d                                         # start the hourly scheduler
```

Local development:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

The original operating guide in Spanish (deployment phases, Telegram setup, live-mode checklist) is in [`LEEME.md`](LEEME.md).

---

<sub>© Óscar López-Boado. All rights reserved.</sub>
