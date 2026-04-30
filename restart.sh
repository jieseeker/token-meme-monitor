#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
STREAMLIT_BIN="${STREAMLIT_BIN:-$ROOT_DIR/.venv/bin/streamlit}"
RUNTIME_DIR="${RUNTIME_DIR:-/tmp/token-meme-monitor}"
LOG_DIR="$RUNTIME_DIR/logs"
PID_DIR="$RUNTIME_DIR/pids"
DASHBOARD_HOST="${DASHBOARD_HOST:-127.0.0.1}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8501}"
LOG_MAX_BYTES="${LOG_MAX_BYTES:-10485760}"
SCHEDULED_BACKTEST_INTERVAL_SECONDS="${SCHEDULED_BACKTEST_INTERVAL_SECONDS:-14400}"
SCHEDULED_MAX_PRICE_DIVERGENCE_PCT="${SCHEDULED_MAX_PRICE_DIVERGENCE_PCT:-0.10}"
SCHEDULED_REFRESH_OUTCOME_LIMIT="${SCHEDULED_REFRESH_OUTCOME_LIMIT:-5000}"

WORKER_PID="$PID_DIR/worker.pid"
SCHEDULED_PID="$PID_DIR/scheduled-backtest.pid"
DASHBOARD_PID="$PID_DIR/dashboard.pid"

mkdir -p "$LOG_DIR" "$PID_DIR"

require_runtime() {
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Missing Python runtime: $PYTHON_BIN" >&2
    exit 1
  fi
  if [[ ! -x "$STREAMLIT_BIN" ]]; then
    echo "Missing Streamlit runtime: $STREAMLIT_BIN" >&2
    exit 1
  fi
}

pid_is_running() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

stop_pid_file() {
  local pid_file="$1"
  local pid=""
  if [[ -f "$pid_file" ]]; then
    pid="$(<"$pid_file")"
    if pid_is_running "$pid"; then
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
  fi
}

stop_pattern() {
  local pattern="$1"
  local pids=""
  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    for pid in $pids; do
      if [[ "$pid" != "$$" ]] && pid_is_running "$pid"; then
        kill "$pid" 2>/dev/null || true
      fi
    done
  fi
}

stop_services() {
  echo "Stopping token-meme-monitor services..."
  stop_pid_file "$WORKER_PID"
  stop_pid_file "$SCHEDULED_PID"
  stop_pid_file "$DASHBOARD_PID"
  stop_pattern "[t]oken_meme_monitor run-worker"
  stop_pattern "[t]oken_meme_monitor run-scheduled-backtest-worker"
  stop_pattern "[s]treamlit run .*dashboard/app.py"
  sleep 2
}

file_size_bytes() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "0"
    return
  fi
  stat -f%z "$path" 2>/dev/null || stat -c%s "$path" 2>/dev/null || echo "0"
}

rotate_log_file() {
  local log_file="$1"
  local size
  size="$(file_size_bytes "$log_file")"
  if [[ "$size" -gt "$LOG_MAX_BYTES" ]]; then
    mv -f "$log_file" "$log_file.1"
    : > "$log_file"
    echo "rotated $log_file size=$size max=$LOG_MAX_BYTES"
  fi
}

rotate_logs() {
  mkdir -p "$LOG_DIR"
  rotate_log_file "$LOG_DIR/worker.log"
  rotate_log_file "$LOG_DIR/scheduled-backtest.log"
  rotate_log_file "$LOG_DIR/dashboard.log"
}

start_service() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"
  shift 3

  echo "Starting $name..."
  : > "$log_file"
  nohup "$@" >> "$log_file" 2>&1 &
  local pid="$!"
  echo "$pid" > "$pid_file"
  sleep 1

  if ! pid_is_running "$pid"; then
    echo "$name failed to start. Log: $log_file" >&2
    tail -n 80 "$log_file" >&2 || true
    exit 1
  fi

  echo "$name pid=$pid log=$log_file"
}

start_services() {
  require_runtime
  rotate_logs
  start_service "worker" "$WORKER_PID" "$LOG_DIR/worker.log" \
    "$PYTHON_BIN" -m token_meme_monitor run-worker
  start_service "scheduled-backtest" "$SCHEDULED_PID" "$LOG_DIR/scheduled-backtest.log" \
    "$PYTHON_BIN" -m token_meme_monitor run-scheduled-backtest-worker \
      --interval-seconds "$SCHEDULED_BACKTEST_INTERVAL_SECONDS" \
      --max-price-divergence-pct "$SCHEDULED_MAX_PRICE_DIVERGENCE_PCT" \
      --refresh-outcome-limit "$SCHEDULED_REFRESH_OUTCOME_LIMIT"
  start_service "dashboard" "$DASHBOARD_PID" "$LOG_DIR/dashboard.log" \
    "$STREAMLIT_BIN" run dashboard/app.py \
      --server.headless true \
      --server.address "$DASHBOARD_HOST" \
      --server.port "$DASHBOARD_PORT"
  echo "Dashboard: http://$DASHBOARD_HOST:$DASHBOARD_PORT"
}

fallback_status() {
  for item in \
    "worker:$WORKER_PID" \
    "scheduled-backtest:$SCHEDULED_PID" \
    "dashboard:$DASHBOARD_PID"; do
    local name="${item%%:*}"
    local pid_file="${item#*:}"
    local pid=""
    if [[ -f "$pid_file" ]]; then
      pid="$(<"$pid_file")"
    fi
    if pid_is_running "$pid"; then
      echo "$name running pid=$pid"
    else
      echo "$name stopped"
    fi
  done
}

print_status() {
  if [[ -x "$PYTHON_BIN" ]]; then
    "$PYTHON_BIN" -m token_meme_monitor runtime-status \
      --runtime-dir "$RUNTIME_DIR" \
      --dashboard-host "$DASHBOARD_HOST" \
      --dashboard-port "$DASHBOARD_PORT" || fallback_status
  else
    fallback_status
  fi
}

command="${1:-restart}"
case "$command" in
  restart)
    stop_services
    start_services
    ;;
  start)
    start_services
    ;;
  stop)
    stop_services
    ;;
  status)
    print_status
    ;;
  rotate-logs)
    rotate_logs
    ;;
  *)
    echo "Usage: $0 [restart|start|stop|status|rotate-logs]" >&2
    exit 2
    ;;
esac
