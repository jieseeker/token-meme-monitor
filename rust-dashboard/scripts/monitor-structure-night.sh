#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-http://43.133.91.45:8501}"
start_epoch="${2:?start epoch required}"
end_epoch="${3:?end epoch required}"
out_dir="${4:?output directory required}"
interval_seconds="${5:-300}"

mkdir -p "$out_dir"
rounds_file="$out_dir/rounds.tsv"
issues_file="$out_dir/issues.tsv"

printf 'time\tstructure_http\tstructure_items\tsignal_http\tsignal_summaries\ttokens_http\ttokens_items\twatch_missing_b_crossed\tstage_or_crossing_mismatch\n' > "$rounds_file"
printf 'time\tissue\n' > "$issues_file"

while (( $(date +%s) < start_epoch )); do
  sleep 30
done

while (( $(date +%s) <= end_epoch )); do
  now="$(date +%Y-%m-%dT%H:%M:%S%z)"
  stamp="$(date +%Y%m%d-%H%M%S)"
  structure_file="$out_dir/structure-$stamp.json"
  signal_file="$out_dir/signal-v2-$stamp.json"
  tokens_file="$out_dir/tokens-$stamp.json"

  structure_http="$(curl -sS -o "$structure_file" -w '%{http_code}' "$base_url/api/structure?limit=500" || true)"
  signal_http="$(curl -sS -o "$signal_file" -w '%{http_code}' "$base_url/api/signal-v2?limit=500" || true)"
  tokens_http="$(curl -sS -o "$tokens_file" -w '%{http_code}' "$base_url/api/tokens?limit=500" || true)"

  structure_items="$(jq '.items | length' "$structure_file" 2>/dev/null || echo 0)"
  signal_summaries="$(jq '.summaries | length' "$signal_file" 2>/dev/null || echo 0)"
  tokens_items="$(jq 'length' "$tokens_file" 2>/dev/null || echo 0)"
  watch_missing_b_crossed="$(jq '[.items[] | select(.stage == "watch" and .b_crossed_at == null)] | length' "$structure_file" 2>/dev/null || echo 0)"
  stage_or_crossing_mismatch="$(jq --slurpfile signal "$signal_file" '
    [ .items[] as $item
      | ($signal[0].summaries[] | select(.token_address == $item.token_address)) as $summary
      | select($item.stage != $summary.stage or (($item.b_crossed_at // null) != ($summary.b_crossed_at // null)))
    ] | length
  ' "$structure_file" 2>/dev/null || echo 0)"

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$now" "$structure_http" "$structure_items" "$signal_http" "$signal_summaries" \
    "$tokens_http" "$tokens_items" "$watch_missing_b_crossed" "$stage_or_crossing_mismatch" >> "$rounds_file"

  if [[ "$structure_http" != "200" ]]; then
    printf '%s\tstructure_http_%s\n' "$now" "$structure_http" >> "$issues_file"
  fi
  if [[ "$signal_http" != "200" ]]; then
    printf '%s\tsignal_http_%s\n' "$now" "$signal_http" >> "$issues_file"
  fi
  if [[ "$tokens_http" != "200" ]]; then
    printf '%s\ttokens_http_%s\n' "$now" "$tokens_http" >> "$issues_file"
  fi
  if (( structure_items == 0 )); then
    printf '%s\tstructure_empty\n' "$now" >> "$issues_file"
  fi
  if (( signal_summaries == 0 )); then
    printf '%s\tsignal_empty\n' "$now" >> "$issues_file"
  fi
  if (( tokens_items == 0 )); then
    printf '%s\ttokens_empty\n' "$now" >> "$issues_file"
  fi
  if (( watch_missing_b_crossed > 0 )); then
    printf '%s\twatch_missing_b_crossed=%s\n' "$now" "$watch_missing_b_crossed" >> "$issues_file"
  fi
  if (( stage_or_crossing_mismatch > 0 )); then
    printf '%s\tstage_or_crossing_mismatch=%s\n' "$now" "$stage_or_crossing_mismatch" >> "$issues_file"
  fi

  sleep "$interval_seconds"
done
