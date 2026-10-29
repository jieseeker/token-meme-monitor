#!/usr/bin/env bash
set -euo pipefail
BASE=/home/ubuntu/token-meme-monitor
OUT="$BASE/monitoring-runtime/continuous/exit-replay"
mkdir -p "$OUT"
while true; do
  stamp=$(date -u +%Y%m%d-%H%M%S)
  tmp="$OUT/exit-replay-$stamp.json.next"
  report="$OUT/exit-replay-$stamp.json"
  MONITOR_DATABASE_PATH="$BASE/data/monitor.db" "$BASE/tmm" signal-v2-replay > "$tmp"
  mv "$tmp" "$report"
  python3 - "$report" "$OUT/summary.tsv" <<'PY'
import json, os, sys
from datetime import datetime
report_path, summary_path = sys.argv[1:]
with open(report_path, encoding="utf-8") as handle:
    report = json.load(handle)
row = {
    "captured_at": int(datetime.fromisoformat(report["window"]["replay_until"].replace("Z", "+00:00")).timestamp()),
    "mode": report["mode"],
}
for stage in ("A", "B", "C"):
    for strategy, outcome in report["structural_hits"][stage]["exit_strategies"].items():
        prefix = f"{stage}_{strategy}"
        for key in ("denominator", "resolved", "wins", "losses", "mean_net_return_pct", "profit_factor"):
            row[f"{prefix}_{key}"] = outcome[key]
if not os.path.exists(summary_path):
    with open(summary_path, "w", encoding="utf-8") as handle:
        handle.write("\t".join(row.keys()) + "\n")
with open(summary_path, "a", encoding="utf-8") as handle:
    handle.write("\t".join(str(value) for value in row.values()) + "\n")
PY
  now=$(date +%s)
  next_at=$(( ((now / 10800) + 1) * 10800 ))
  sleep "$((next_at - now))"
done
