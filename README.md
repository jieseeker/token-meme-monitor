# Token Meme Monitor

Long-lived BSC meme token monitoring stack with:

- PancakeSwap V2 `PairCreated` discovery via on-chain block scanning
- DexScreener pair snapshots as an external market-data enhancer
- Configurable rule engine with reasons, risk flags, and lifecycle states
- Telegram alert deduplication with cooldowns
- Streamlit dashboard reading from the same local database

This repository implements the first production-minded slice of the long-term plan:

- `discovery pool`: minute-level discovery and tracking of newly created pairs
- `focus pool`: higher-frequency refresh for stronger candidates
- `signals`: score + reasons + risk flags + alert candidate output
- dashboard history uses locally cached external interval returns for 2h / 24h review instead of local outcome labels
- historical GeckoTerminal OHLCV used by validation is cached in SQLite after first fetch

The default monitoring universe now targets `Binance Alpha` tokens on BSC instead of all newly created BSC pairs.

## Project layout

```text
token-meme-monitor/
├── dashboard/
│   └── app.py
├── tests/
├── token_meme_monitor/
│   ├── clients/
│   ├── alerts.py
│   ├── cli.py
│   ├── config.py
│   ├── constants.py
│   ├── database.py
│   ├── features.py
│   ├── logging_config.py
│   ├── models.py
│   ├── orchestrator.py
│   ├── signals.py
│   └── utils.py
├── .env.example
├── main.py
├── pyproject.toml
└── requirements.txt
```

## Quick start

1. Create a virtual environment and install dependencies.
2. Copy `.env.example` to `.env` and fill in the values you want to override.
3. Initialize the database.
4. Run the worker.
5. Run the scheduled backtest worker in a separate shell.
6. Run the dashboard in a separate shell.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 -m token_meme_monitor healthcheck
python3 -m token_meme_monitor init-db
python3 -m token_meme_monitor run-worker
python3 -m token_meme_monitor run-scheduled-backtest-worker
streamlit run dashboard/app.py
```

Recommended local runtime uses three terminals:

| Service | Command | Responsibility | Main dependencies | Output |
| --- | --- | --- | --- | --- |
| Realtime monitor worker | `./.venv/bin/python -m token_meme_monitor run-worker` | Discovers new pools, refreshes market data, computes signals, writes predictions | BSC RPC, Binance Alpha, DexScreener, GeckoTerminal | SQLite `pairs`, `snapshots`, `signals`, `signal_predictions` |
| Scheduled backtest worker | `./.venv/bin/python -m token_meme_monitor run-scheduled-backtest-worker` | Refreshes mature outcomes every 4 hours, runs backtests, writes missed/chase reports | Local SQLite, GeckoTerminal when outcome backfill is needed | `data/backtests/scheduled/latest.md`, `latest.json` |
| Dashboard | `./.venv/bin/streamlit run dashboard/app.py` | Shows candidate list, token detail, prediction, history, and trend views | Local SQLite; detail trend reads local cache first and may call GeckoTerminal on misses | `http://127.0.0.1:8501` |

```bash
# Terminal 1: realtime monitor worker
./.venv/bin/python -m token_meme_monitor run-worker
```

```bash
# Terminal 2: scheduled backtest worker, every 4 hours
./.venv/bin/python -m token_meme_monitor run-scheduled-backtest-worker \
  --interval-seconds 14400 \
  --max-price-divergence-pct 0.10 \
  --refresh-outcome-limit 1000
```

```bash
# Terminal 3: dashboard
./.venv/bin/streamlit run dashboard/app.py
```

## Commands

```bash
python3 -m token_meme_monitor init-db
python3 -m token_meme_monitor print-config
python3 -m token_meme_monitor healthcheck
python3 -m token_meme_monitor run-worker --once
python3 -m token_meme_monitor run-worker
python3 -m token_meme_monitor run-scheduled-backtest-worker --once
python3 -m token_meme_monitor run-scheduled-backtest-worker
python3 -m token_meme_monitor cleanup-data
python3 -m token_meme_monitor validate-token-list
```

## Default behavior

- Uses PancakeSwap V2 factory events on BNB Smart Chain.
- Seeds and refreshes BSC tokens from the official Binance Alpha token list.
- Tracks only pairs where one side is an allowlisted quote asset (`WBNB`, `USDT`, `USDC`, `BUSD` by default).
- Stores all discovery, snapshot, signal, and alert data in SQLite.
- Runs safely without Telegram if bot credentials are omitted.

## Configuration

All runtime settings are environment-driven. Important variables:

- `BSC_RPC_URL`
- `BSC_RPC_URLS`
- `MONITOR_DATABASE_PATH`
- `DISCOVERY_START_BLOCK`
- `DISCOVERY_INITIAL_BACKFILL_BLOCKS`
- `BASE_POLL_INTERVAL_SECONDS`
- `FOCUS_POLL_INTERVAL_SECONDS`
- `ALERT_SCORE_THRESHOLD`
- `FOCUS_SCORE_THRESHOLD`
- `BINANCE_FUTURES_REGISTRY_REFRESH_HOURS`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

See `.env.example` for the full list.

`BSC_RPC_URL` / `BSC_RPC_URLS` matter more than any other setting. Public endpoints differ a lot on:

- `eth_getLogs` limits
- rate limiting
- DNS reliability
- archival depth

The default `.env.example` value uses `https://bnb.rpc.subquery.network/public` because the official `dataseed` public endpoints were observed to reject `eth_getLogs` discovery traffic in live testing.

Set `BSC_RPC_URLS` to a comma-separated endpoint pool when you have multiple RPC providers. The worker cools down endpoints that return provider-limit errors such as `429` / `418`, then switches discovery to the next available endpoint. Binance futures registry labels are cached in SQLite and refreshed only every `BINANCE_FUTURES_REGISTRY_REFRESH_HOURS`; failed refreshes use the last cached registry when available.

Run `python3 -m token_meme_monitor healthcheck` before the worker. If discovery stalls or raises provider-limit errors, switch to a more reliable RPC before changing scoring logic.

## Current scope and intentional gaps

This version is meant to be a stable V1 base, not a finished trading-grade stack.

信号策略基线文档：

- [docs/signal-indicator-baseline.md](/Users/zjj/vs_code/token-meme-monitor/docs/signal-indicator-baseline.md)
- [docs/backend-core-logic.md](/Users/zjj/vs_code/token-meme-monitor/docs/backend-core-logic.md)
- [docs/session-prompt-templates.md](/Users/zjj/vs_code/token-meme-monitor/docs/session-prompt-templates.md)
- [docs/frontend-dashboard-ui.md](/Users/zjj/vs_code/token-meme-monitor/docs/frontend-dashboard-ui.md)

- Included:
  - block-cursor discovery
  - persistent snapshots
  - rule-based scoring
  - alert cooldowns
  - dashboard
- Not yet included:
  - contract-level honeypot and tax simulation
  - owner privilege inspection
  - LP lock / burn verification
  - multi-chain support
  - PostgreSQL migration path

The data model and worker boundaries are set up so those pieces can be added later without rewriting the core pipeline.
