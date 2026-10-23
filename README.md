# Token Meme Monitor (Rust)

Binance Alpha / BSC meme token monitoring stack, fully rewritten in Rust.

Single ~2.5MB binary that monitors the Binance Alpha BSC universe, refreshes pair market data, writes local SQLite history, computes rule signals, and serves a decision-focused dashboard.

## Architecture

```
┌────────────────────────────────────────────────┐
│              tmm binary (~2.5MB)               │
├────────────────┬───────────────────────────────┤
│   Worker       │   Dashboard (axum)            │
│   - Binance α  │   - Token list + detail       │
│   - DexScreener│   - Decision summary          │
│   - Signals    │   - Probability predictions   │
│   - SQLite WAL │   - Price/volume charts       │
├────────────────┴───────────────────────────────┤
│         SQLite (data/monitor.db)               │
└────────────────────────────────────────────────┘
```

## Project Layout

```
token-meme-monitor/
├── rust-dashboard/
│   ├── Cargo.toml
│   ├── src/
│   │   ├── main.rs           # Entry: all / worker / dashboard / init-db
│   │   ├── config.rs         # Environment-driven configuration
│   │   ├── db.rs             # SQLite schema + CRUD + queries
│   │   ├── dashboard.rs      # Axum HTTP server + JSON API
│   │   ├── engine/
│   │   │   ├── features.rs   # Feature vector construction
│   │   │   ├── signals.rs    # Rule-based signal scoring (v1)
│   │   │   └── worker.rs     # Main monitoring loop
│   │   └── clients/
│   │       ├── binance_alpha.rs
│   │       └── dexscreener.rs
│   └── static/index.html     # Embedded frontend (decision workbench)
├── data/                      # SQLite (git-ignored)
├── docs/
├── openspec/                  # Historical change archive
├── .env.example
├── Dockerfile
└── docker-compose.yml
```

## Quick Start

```bash
# Build
cd rust-dashboard
cargo build --release

# Initialize database
MONITOR_DATABASE_PATH=../data/monitor.db ./target/release/tmm init-db

# Run dashboard only
MONITOR_DATABASE_PATH=../data/monitor.db ./target/release/tmm dashboard

# Run worker only
MONITOR_DATABASE_PATH=../data/monitor.db ./target/release/tmm worker

# Run everything (worker + dashboard in one process)
MONITOR_DATABASE_PATH=../data/monitor.db ./target/release/tmm all
```

Dashboard runs at `http://127.0.0.1:8501`.
If a managed `tmm all` service already owns that port, restart that service after a rebuild instead of starting a second process. Use the same `MONITOR_DATABASE_PATH` for every run; otherwise the page and worker can read different databases.

## Docker Deployment

```bash
docker compose up -d
```

See [docs/deploy-fnos-docker.md](docs/deploy-fnos-docker.md) for fnOS NAS deployment.

## Cloud deployment without Docker

The cloud server needs Linux with systemd, Rust/Cargo, a C compiler and the OpenSSL development headers (for example, `build-essential pkg-config libssl-dev` on Debian/Ubuntu). The SSH user needs passwordless `sudo` to install the system service. Cargo builds on the server, so its CPU architecture can differ from this Mac.

```bash
cp .env.example .env                 # set any required runtime values locally
./deploy-cloud.sh --dry-run deploy@example.com /home/deploy/token-meme-monitor
./deploy-cloud.sh deploy@example.com /home/deploy/token-meme-monitor
```

Use `SSH_PORT=2222 ./deploy-cloud.sh ...` for a nondefault SSH port. The script builds the binary remotely, installs a `tmm.service` systemd unit, and leaves the server's `.env` and `data/` directory untouched. On first deployment it creates `.env` from `.env.example`. SQLite defaults to `data/monitor.db` under the server deployment directory.

For the production server, run `./deploy-production.sh`. It uses `~/.ssh/tencent_ed25519` for passwordless SSH (override with `SSH_IDENTITY_FILE=/path/to/key`). It copies only `BSC_DEPTH_RPC_URLS` from the local `.env` (override the source with `TMM_RPC_SOURCE=/path/to/env`); the RPC key stays out of Git. It reads the local `Basic-763618` subscription (override with `TMM_PROXY_SOURCE=/path/to/file`), installs one headless sing-box process on the server, probes each node, and puts only reachable, distinct exit IPs into `GECKOTERMINAL_PROXY_URLS`. The subscription credentials stay in a private server config; the proxy endpoints listen only on `127.0.0.1`. This production command updates those two `.env` entries and observes both services for 30 minutes after deployment. Set `TMM_OBSERVE_MINUTES=0` to skip observation.

```bash
ssh deploy@example.com 'sudo journalctl -u tmm.service -f -n 100'
```

Logs are JSON lines with UTC timestamps, event names and context. systemd captures them in journald; retention follows the server's journald settings. `LOG_LEVEL` defaults to `INFO` and supports `ERROR`, `WARN`, `INFO` and `DEBUG`.

## Configuration

Runtime settings are environment-driven. Copy `.env.example` to `.env` and override as needed.

Key variables:
- `MONITOR_DATABASE_PATH` — SQLite path (default: `data/monitor.db`)
- `WORKER_LOOP_SECONDS` — Worker cycle interval (default: 15)
- `DASHBOARD_PORT` — Dashboard port (default: 8501)
- `BSC_DEPTH_RPC_URLS` — Optional comma separated BSC HTTPS RPC URLs for pool depth; public backups are built in. Keep provider keys in the server's private `.env`.
- `MIN_LIQUIDITY_USD` — Minimum liquidity threshold
- `MIN_VOLUME_H1_USD` — Minimum 1h volume
- `FOCUS_SCORE_THRESHOLD` — Focus state threshold (default: 65)
- `ALERT_SCORE_THRESHOLD` — Alert state threshold (default: 78)

## Signal Engine

Rule-based scoring (strategy v1), ported exactly from Python. Rules include:
- Liquidity band scoring (healthy/ample/insufficient)
- Volume support (1h and 5m impulse)
- Buy dominance (5m and 1h)
- Volume-to-liquidity breakout
- Liquidity-to-FDV balance
- Project metadata presence
- Price trend confirmation
- Binance Alpha specific: alpha score, holder depth, futures listing
- Risk penalties: sell pressure, FDV stretch, missing price

## Performance

| Metric | Python (previous) | Rust (current) |
| --- | --- | --- |
| Dashboard memory | ~450MB (Streamlit) | ~15MB |
| Worker memory | ~350MB (web3) | ~20MB |
| Binary size | ~400MB (image) | ~2.5MB |
| Page response | 200-800ms | <1ms |
| Startup time | 5-15s | <100ms |

## Testing

```bash
cd rust-dashboard
cargo test
```

## Migration from Python

The Rust version reads/writes the same SQLite schema as the Python version. To migrate:
1. Keep your existing `data/monitor.db`
2. Build and run the Rust binary with the same `MONITOR_DATABASE_PATH`
3. Historical data is preserved

## Documentation

- [docs/deploy-fnos-docker.md](docs/deploy-fnos-docker.md): NAS deployment guide
- `openspec/changes/`: Historical change archive and project memory
