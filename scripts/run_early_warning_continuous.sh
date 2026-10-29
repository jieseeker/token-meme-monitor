#!/usr/bin/env bash
set -euo pipefail
BASE=/home/ubuntu/token-meme-monitor
OUT="$BASE/monitoring-runtime/continuous/early-warning"
mkdir -p "$OUT"
while true; do
  stamp=$(date -u +%Y%m%d-%H%M%S)
  exec 200>"$OUT/.lock"
  if flock -n 200; then
    tmp="$OUT/early-warning-$stamp.json.next"
    report="$OUT/early-warning-$stamp.json"
    if MONITOR_DATABASE_PATH="$BASE/data/monitor.db" "$BASE/tmm" early-warning-replay > "$tmp"; then
      mv "$tmp" "$report"
      find "$OUT" -maxdepth 1 -type f -name "early-warning-*.json" -mmin +20160 -delete
    else
      rm -f "$tmp"
    fi
  fi
  now=$(date +%s)
  next_at=$(( ((now / 10800) + 1) * 10800 ))
  sleep "$((next_at - now))"
done
