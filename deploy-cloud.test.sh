#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
plan=$(bash "$root/deploy-cloud.sh" --dry-run deploy@example.com /srv/token-meme-monitor)
[[ $plan == *"rust-dashboard/src"* && $plan == *"rust-dashboard/static"* ]]
if [[ $plan == *"rust-dashboard/static .env"* ]]; then
  echo "Deploy would overwrite the server environment" >&2
  exit 1
fi
[[ $plan != *"data/monitor.db"* && $plan != *"rust-dashboard/target"* ]]
[[ $plan == *"Cargo and runs with systemd"* && $plan != *"Docker"* ]]
if bash "$root/deploy-cloud.sh" --dry-run 'deploy@example.com;false' /srv/token-meme-monitor >/dev/null 2>&1; then
  echo "Unsafe SSH target was accepted" >&2
  exit 1
fi
if bash "$root/deploy-cloud.sh" --dry-run deploy@example.com /srv/../other >/dev/null 2>&1; then
  echo "Unsafe server path was accepted" >&2
  exit 1
fi
if SSH_PORT=65536 bash "$root/deploy-cloud.sh" --dry-run deploy@example.com /srv/token-meme-monitor >/dev/null 2>&1; then
  echo "Invalid SSH port was accepted" >&2
  exit 1
fi

fake_bin=$(mktemp -d)
trap 'rm -rf "$fake_bin"' EXIT
cat >"$fake_bin/ssh" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$SSH_CALLS"
cat >>"${SSH_INPUT:-/dev/null}"
MOCK
chmod +x "$fake_bin/ssh"
SSH_CALLS="$fake_bin/calls" PATH="$fake_bin:$PATH" bash "$root/deploy-cloud.sh" deploy@example.com /srv/token-meme-monitor >/dev/null
control_paths=$(rg -o 'ControlPath=[^ ]+' "$fake_bin/calls" | sort -u)
[[ -n $control_paths && $control_paths != *$'\n'* ]]
[[ $(rg -c 'ControlMaster=auto' "$fake_bin/calls") == 4 ]]

printf 'BSC_DEPTH_RPC_URLS=https://rpc.example.test/private-key\n' >"$fake_bin/rpc.env"
SSH_CALLS="$fake_bin/calls" SSH_INPUT="$fake_bin/input" TMM_RPC_SOURCE="$fake_bin/rpc.env" \
  PATH="$fake_bin:$PATH" bash "$root/deploy-production.sh" --sync-rpc-only >/dev/null
[[ $(cat "$fake_bin/input") == 'BSC_DEPTH_RPC_URLS=https://rpc.example.test/private-key' ]]
before=$(wc -l <"$fake_bin/calls")
SSH_CALLS="$fake_bin/calls" SSH_INPUT="$fake_bin/input" TMM_RPC_SOURCE="$fake_bin/missing" \
  PATH="$fake_bin:$PATH" bash "$root/deploy-production.sh" --dry-run >/dev/null
[[ $(wc -l <"$fake_bin/calls") == "$before" ]]
