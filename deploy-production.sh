#!/usr/bin/env bash
set -euo pipefail

proxy_source=${TMM_PROXY_SOURCE:-/Users/jieseeker/Desktop/Basic-763618}
export SSH_IDENTITY_FILE=${SSH_IDENTITY_FILE:-/Users/jieseeker/.ssh/tencent_ed25519}
local_env=${TMM_RPC_SOURCE:-$(dirname "$0")/.env}
if [[ ${1:-} != --dry-run ]]; then
  [[ -r $local_env ]] || { echo 'Local RPC env file is not readable' >&2; exit 2; }
  rpc_line=$(grep '^BSC_DEPTH_RPC_URLS=' "$local_env" | tail -1 || true)
  [[ $rpc_line == BSC_DEPTH_RPC_URLS=https://* ]] || { echo 'Missing or invalid BSC_DEPTH_RPC_URLS in local env' >&2; exit 2; }
  printf '%s\n' "$rpc_line" | ssh -i "$SSH_IDENTITY_FILE" -o IdentitiesOnly=yes -o BatchMode=yes ubuntu@43.131.242.70 '
      set -eu
      umask 077
      cd /home/ubuntu/token-meme-monitor
      IFS= read -r rpc_line
      case "$rpc_line" in BSC_DEPTH_RPC_URLS=https://*) ;; *) exit 2 ;; esac
      next=$(mktemp .env.XXXXXX)
      grep -v "^BSC_DEPTH_RPC_URLS=" .env > "$next" || true
      printf "%s\n" "$rpc_line" >> "$next"
      chmod 600 "$next"
      mv -f "$next" .env
    '
  echo 'BSC RPC endpoints synchronized from local .env.'
fi
if [[ ${1:-} == --sync-rpc-only ]]; then exit 0; fi
exec "$(dirname "$0")/deploy-cloud.sh" --git-source https://github.com/jieseeker/token-meme-monitor.git --proxy-config "$proxy_source" --observe-minutes "${TMM_OBSERVE_MINUTES:-30}" "$@" ubuntu@43.131.242.70 /home/ubuntu/token-meme-monitor
