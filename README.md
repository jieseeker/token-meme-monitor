# Token Meme Monitor

Binance Alpha / BSC meme token monitoring stack.

The current project is not a generic all-chain pair scanner. It monitors the Binance Alpha BSC universe, refreshes pair market data, writes local SQLite history, computes rule signals and p4 prediction scores, and exposes the result through a Streamlit dashboard plus scheduled backtest reports.

## Project Layout

```text
token-meme-monitor/
├── dashboard/
│   ├── app.py
│   └── view_models.py
├── docs/
│   ├── backend-core-logic.md
│   ├── frontend-dashboard-ui.md
│   └── session-prompt-templates.md
├── openspec/
│   └── changes/
├── tests/
├── token_meme_monitor/
│   ├── clients/
│   ├── cli.py
│   ├── config.py
│   ├── database.py
│   ├── health.py
│   ├── orchestrator.py
│   ├── prediction_backtest.py
│   ├── prediction_outcomes.py
│   ├── predictions.py
│   ├── scheduled_backtest.py
│   └── signals.py
├── .env.example
├── restart.sh
├── requirements.txt
└── pyproject.toml
```

`data/` contains local SQLite data and generated reports. It is ignored by git and is not source documentation.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
./.venv/bin/python -m token_meme_monitor init-db
```

Start or restart the local runtime:

```bash
./restart.sh
```

Check runtime status:

```bash
./restart.sh status
```

Stop runtime services:

```bash
./restart.sh stop
```

The dashboard runs at:

```text
http://127.0.0.1:8501
```

## Runtime Services

The local runtime has three processes:

| Service | Command | Responsibility |
| --- | --- | --- |
| Realtime worker | `./.venv/bin/python -m token_meme_monitor run-worker` | Refreshes Alpha universe, pair snapshots, signals, predictions, outcomes, holder side job |
| Scheduled backtest worker | `./.venv/bin/python -m token_meme_monitor run-scheduled-backtest-worker` | Refreshes mature prediction outcomes and writes scheduled reports |
| Dashboard | `./.venv/bin/streamlit run dashboard/app.py` | Reads local SQLite and renders candidate list, token detail, predictions, history, and trends |

`restart.sh` starts all three in the background and writes logs/PIDs under `/tmp/token-meme-monitor/`. `./restart.sh status` uses the structured runtime status command and reports stale PID files, command mismatches, log paths, log sizes, and the dashboard URL. `./restart.sh rotate-logs` rotates local runtime logs when they exceed `LOG_MAX_BYTES` (default 10 MB).

## CLI Commands

```bash
./.venv/bin/python -m token_meme_monitor init-db
./.venv/bin/python -m token_meme_monitor print-config
./.venv/bin/python -m token_meme_monitor healthcheck
./.venv/bin/python -m token_meme_monitor health-report
./.venv/bin/python -m token_meme_monitor health-report --json
./.venv/bin/python -m token_meme_monitor runtime-status
./.venv/bin/python -m token_meme_monitor runtime-status --json
./.venv/bin/python -m token_meme_monitor lifecycle-inventory --json
./.venv/bin/python -m token_meme_monitor lifecycle-integrity --json
./.venv/bin/python -m token_meme_monitor retention-plan --older-than-days 14 --json
./.venv/bin/python -m token_meme_monitor run-worker --once
./.venv/bin/python -m token_meme_monitor run-worker
./.venv/bin/python -m token_meme_monitor cleanup-data
./.venv/bin/python -m token_meme_monitor validate-token-list
./.venv/bin/python -m token_meme_monitor export-prediction-dataset
./.venv/bin/python -m token_meme_monitor refresh-prediction-outcomes
./.venv/bin/python -m token_meme_monitor refresh-prediction-outcomes --refresh-missing-quality
./.venv/bin/python -m token_meme_monitor refresh-risk-enrichment --fixture-json risk-fixture.json
./.venv/bin/python -m token_meme_monitor rebuild-predictions
./.venv/bin/python -m token_meme_monitor backtest-predictions
./.venv/bin/python -m token_meme_monitor strategy-feedback-report
./.venv/bin/python -m token_meme_monitor scheduled-backtest-report
./.venv/bin/python -m token_meme_monitor run-scheduled-backtest-worker --once
./.venv/bin/python -m token_meme_monitor run-scheduled-backtest-worker
./.venv/bin/python -m token_meme_monitor compact-history --older-than-days 14 --dry-run
./.venv/bin/python -m token_meme_monitor compact-history --older-than-days 14 --execute
```

## Configuration

Runtime settings are environment-driven. Copy `.env.example` to `.env` and override as needed.

Important variables:

- `MONITOR_DATABASE_PATH`
- `MONITOR_UNIVERSE`
- `BSC_RPC_URL`
- `BSC_RPC_URLS`
- `DISCOVERY_START_BLOCK`
- `DISCOVERY_INITIAL_BACKFILL_BLOCKS`
- `DISCOVERY_BLOCK_CHUNK_SIZE`
- `DISCOVERY_BLOCK_CONFIRMATIONS`
- `WORKER_LOOP_SECONDS`
- `MAX_PAIRS_PER_CYCLE`
- `BASE_POLL_INTERVAL_SECONDS`
- `FOCUS_POLL_INTERVAL_SECONDS`
- `MIN_LIQUIDITY_USD`
- `ARCHIVE_LIQUIDITY_USD`
- `MIN_VOLUME_H1_USD`
- `FOCUS_SCORE_THRESHOLD`
- `ALERT_SCORE_THRESHOLD`
- `BINANCE_ALPHA_REFRESH_MINUTES`
- `BINANCE_ALPHA_PAIR_SEED_REFRESH_MINUTES`
- `BINANCE_ALPHA_SEED_BATCH_SIZE`
- `HOLDER_METRICS_JOB_INTERVAL_SECONDS`
- `HOLDER_METRICS_REFRESH_HOURS`
- `HOLDER_METRICS_BATCH_SIZE`
- `BINANCE_FUTURES_REGISTRY_REFRESH_HOURS`
- `RISK_ENRICHMENT_FIXTURE_PATH`
- `RISK_ENRICHMENT_TTL_HOURS`
- `RISK_ENRICHMENT_BATCH_SIZE`
- `DASHBOARD_AUTO_REFRESH_SECONDS`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

`BSC_RPC_URLS` can contain a comma-separated endpoint pool. The worker cools down RPC endpoints that return provider-limit errors and switches discovery to another endpoint when available.

## Current Behavior

- Default universe is `MONITOR_UNIVERSE=binance_alpha`.
- DexScreener pair snapshots are validated before being written.
- Market data is sanitized before feature generation.
- Signal scoring is rule-based strategy `v1`.
- Prediction scoring is p4 and writes horizon-specific scores: `short_momentum_score`, `continuation_score`, and `breakout_score`.
- Mature prediction outcomes are computed from GeckoTerminal hourly OHLCV and used for calibration/backtesting.
- `strategy-feedback-report` writes versioned feedback runs that compare prediction outcomes by stable slices and emits review-only recommendations.
- Risk enrichment is observation-only: optional providers can write `risk_snapshots`, and health/dashboard read models can display unknown/failure/high-risk state, but scoring and alert gating are unchanged.
- Dashboard main sorting uses `short_momentum_score` with `opportunity_score` as a compatibility fallback.
- Dashboard detail includes a decision workbench view that connects signal, prediction, outcome, and CSV case export; local review notes/watchlist are stored separately from scoring tables.
- `compact-history` archives old raw payloads while keeping prediction dataset reads compatible with compacted rows.
- Lifecycle commands are read-only by default: `lifecycle-inventory`, `lifecycle-integrity`, and `retention-plan` expose database growth, integrity findings, and dry-run cleanup candidates.

## Documentation

- [docs/backend-core-logic.md](docs/backend-core-logic.md): backend, signal, prediction, storage, worker, and CLI baseline
- [docs/frontend-dashboard-ui.md](docs/frontend-dashboard-ui.md): dashboard structure, interaction rules, and UI constraints
- [docs/session-prompt-templates.md](docs/session-prompt-templates.md): short context templates for new coding sessions
- `openspec/changes/`: historical change artifacts and project memory

## Testing

```bash
./.venv/bin/python -m unittest discover -s tests
```

## Intentional Gaps

The current baseline does not yet use observation-only risk enrichment for:

- tax / honeypot risk scoring
- owner permission scoring
- LP lock / burn scoring
- real-time holder concentration scoring
- multi-chain strategy abstraction
- local GPU or deep learning models
- multi-worker concurrent SQLite writes
