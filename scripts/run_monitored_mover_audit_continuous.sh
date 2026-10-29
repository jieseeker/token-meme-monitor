#!/usr/bin/env bash
set -euo pipefail
BASE=/home/ubuntu/token-meme-monitor
OUT="$BASE/monitoring-runtime/continuous/monitored-movers"
mkdir -p "$OUT"
while true; do
  stamp=$(date -u +%Y%m%d-%H%M%S)
  exec 200>"$OUT/.lock"
  if flock -n 200; then
    python3 "$BASE/.source/scripts/monitored_mover_audit.py" \
      --db "$BASE/data/monitor.db" \
      --top 20 \
      --output "$OUT/monitored-movers-$stamp.json"
    find "$OUT" -maxdepth 1 -type f -name 'monitored-movers-*.json' -mmin +20160 -delete
  fi
  now=$(date +%s)
  next_at=$(((now / 3600 + 1) * 3600))
  sleep "$((next_at - now))"
done
