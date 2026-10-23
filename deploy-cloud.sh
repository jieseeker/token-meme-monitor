#!/usr/bin/env bash
set -euo pipefail

dry_run=0
proxy_source=
observe_minutes=0
while (($#)); do
  case $1 in
    --dry-run) dry_run=1; shift ;;
    --proxy-config) proxy_source=${2:?missing proxy config path}; shift 2 ;;
    --observe-minutes) observe_minutes=${2:?missing observation duration}; shift 2 ;;
    *) break ;;
  esac
done
if [[ $# != 2 ]]; then
  echo "Usage: $0 [--dry-run] [--proxy-config YAML] [--observe-minutes N] user@host /absolute/server/path" >&2
  exit 2
fi

target=$1
remote_dir=$2
port=${SSH_PORT:-22}
identity_file=${SSH_IDENTITY_FILE:-}
if [[ ! $target =~ ^[A-Za-z_][A-Za-z0-9_-]*@[A-Za-z0-9.-]+$ ]]; then
  echo "Invalid SSH target: use user@hostname or user@IPv4" >&2
  exit 2
fi
if [[ ! $remote_dir =~ ^/[A-Za-z0-9_./-]+$ || $remote_dir == *..* ]]; then
  echo "Invalid server path: use an absolute path without '..'" >&2
  exit 2
fi
if [[ ! $port =~ ^[1-9][0-9]{0,4}$ ]] || (( 10#$port > 65535 )); then
  echo "SSH_PORT must be between 1 and 65535" >&2
  exit 2
fi
if [[ ! $observe_minutes =~ ^[0-9]+$ ]]; then
  echo 'Observation minutes must be between 0 and 1440' >&2
  exit 2
fi
observe_minutes=$((10#$observe_minutes))
if (( observe_minutes > 1440 )); then
  echo 'Observation minutes must be between 0 and 1440' >&2
  exit 2
fi
if [[ -n $proxy_source && ! -r $proxy_source ]]; then
  echo 'Proxy config file is not readable' >&2
  exit 2
fi
if [[ -n $identity_file && ! -r $identity_file ]]; then
  echo 'SSH identity file is not readable' >&2
  exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
files=(.env.example rust-dashboard/Cargo.toml rust-dashboard/Cargo.lock
  rust-dashboard/src rust-dashboard/static)

if (( dry_run )); then
  printf 'Target: %s:%s (SSH port %s)\nFiles: %s\n' "$target" "$remote_dir" "$port" "${files[*]}"
  echo "Server builds with Cargo and runs with systemd; data/ stays on the server."
  if [[ -n $proxy_source ]]; then echo 'Proxy nodes: local YAML converts to sing-box, then cloud probes unique exit IPs.'; fi
  if (( observe_minutes )); then echo "Observation: $observe_minutes minutes after deployment."; fi
  exit 0
fi

control_dir=$(mktemp -d /tmp/tmm-deploy-ssh.XXXXXX)
control_path=$control_dir/socket
ssh_opts=(-p "$port" -o ControlMaster=auto -o ControlPersist=600 -o "ControlPath=$control_path")
if [[ -n $identity_file ]]; then ssh_opts+=(-i "$identity_file" -o IdentitiesOnly=yes -o BatchMode=yes); fi
cleanup() {
  ssh "${ssh_opts[@]}" -O exit "$target" >/dev/null 2>&1 || true
  rm -f "$control_dir/sing-box.json"
  rmdir "$control_dir" 2>/dev/null || true
}
trap cleanup EXIT

proxy_count=0
if [[ -n $proxy_source ]]; then
  command -v ruby >/dev/null || { echo 'Ruby is required locally to convert the proxy file.' >&2; exit 1; }
  ruby "$root/scripts/generate-sing-box.rb" "$proxy_source" "$control_dir/sing-box.json"
  proxy_count=$(ruby -rjson -e 'puts JSON.parse(File.read(ARGV[0])).fetch("inbounds").length' "$control_dir/sing-box.json")
fi

ssh "${ssh_opts[@]}" "$target" 'bash -s' <<'REMOTE'
set -euo pipefail
if [[ -f "$HOME/.cargo/env" ]]; then source "$HOME/.cargo/env"; fi
if [[ $(uname -s) != Linux || ! -d /run/systemd/system ]] || ! command -v cargo >/dev/null || ! command -v systemctl >/dev/null; then
  echo 'Server needs Linux, Rust/Cargo, and systemd. Install Rust and build prerequisites first.' >&2
  exit 1
fi
if [[ $(id -u) != 0 ]] && ! sudo -n true; then
  echo 'SSH user needs passwordless sudo to install the systemd service.' >&2
  exit 1
fi
REMOTE

tar --no-xattrs -C "$root" -czf - "${files[@]}" |
  ssh "${ssh_opts[@]}" "$target" "mkdir -p '$remote_dir' && tar -xzf - -C '$remote_dir'"

if (( proxy_count )); then
  ssh "${ssh_opts[@]}" "$target" "umask 077; mkdir -p '$remote_dir/.private'; chmod 700 '$remote_dir/.private'; cat > '$remote_dir/.private/sing-box.json.next'" <"$control_dir/sing-box.json"
  ssh "${ssh_opts[@]}" "$target" bash -s -- "$remote_dir" "$proxy_count" <<'REMOTE'
set -euo pipefail
remote_dir=$1
proxy_count=$2
cd "$remote_dir"
sudo_cmd=()
if [[ $(id -u) != 0 ]]; then sudo_cmd=(sudo -n); fi

installed_sing_box=0
if ! command -v sing-box >/dev/null; then
  command -v curl >/dev/null || { echo 'curl is required to install sing-box.' >&2; exit 1; }
  key_file=$(mktemp)
  source_file=$(mktemp)
  trap 'rm -f "$key_file" "$source_file"' EXIT
  curl -fsSL https://sing-box.app/gpg.key -o "$key_file"
  "${sudo_cmd[@]}" mkdir -p /etc/apt/keyrings
  "${sudo_cmd[@]}" install -m 644 "$key_file" /etc/apt/keyrings/sagernet.asc
  cat >"$source_file" <<'APT'
Types: deb
URIs: https://deb.sagernet.org/
Suites: *
Components: *
Enabled: yes
Signed-By: /etc/apt/keyrings/sagernet.asc
APT
  "${sudo_cmd[@]}" install -m 644 "$source_file" /etc/apt/sources.list.d/sagernet.sources
  "${sudo_cmd[@]}" apt-get update -qq
  "${sudo_cmd[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sing-box
  rm -f "$key_file" "$source_file"
  installed_sing_box=1
fi
if (( installed_sing_box )); then "${sudo_cmd[@]}" systemctl disable --now sing-box.service >/dev/null 2>&1 || true; fi
sing_box=$(command -v sing-box)
"$sing_box" check -c .private/sing-box.json.next

unit_file=$(mktemp)
trap 'rm -f "$unit_file"' EXIT
cat >"$unit_file" <<UNIT
[Unit]
Description=Token Meme Monitor Proxy
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$(id -un)
ExecStart=$sing_box run -c $remote_dir/.private/sing-box.json
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT
"${sudo_cmd[@]}" install -m 644 "$unit_file" /etc/systemd/system/tmm-proxy.service
"${sudo_cmd[@]}" systemctl daemon-reload
"${sudo_cmd[@]}" systemctl enable tmm-proxy.service
if [[ -f .private/sing-box.json ]]; then cp -p .private/sing-box.json .private/sing-box.json.previous; fi
mv -f .private/sing-box.json.next .private/sing-box.json
chmod 600 .private/sing-box.json
if ! "${sudo_cmd[@]}" systemctl restart tmm-proxy.service || ! "${sudo_cmd[@]}" systemctl is-active --quiet tmm-proxy.service; then
  if [[ -f .private/sing-box.json.previous ]]; then
    mv -f .private/sing-box.json.previous .private/sing-box.json
    "${sudo_cmd[@]}" systemctl restart tmm-proxy.service || true
  fi
  echo 'Proxy service failed to start; previous config restored when available.' >&2
  exit 1
fi
sleep 2

declare -A exit_ips=()
proxy_urls=()
for ((index=0; index<proxy_count; index++)); do
  port=$((19000 + index))
  if ip=$(curl -fsS --max-time 6 --proxy "http://127.0.0.1:$port" https://api.ipify.org 2>/dev/null) &&
     [[ $ip =~ ^[0-9a-fA-F:.]+$ && -z ${exit_ips[$ip]+present} ]]; then
    exit_ips[$ip]=1
    proxy_urls+=("http://127.0.0.1:$port")
  fi
done
if (( ${#proxy_urls[@]} == 0 )); then
  if [[ -f .private/sing-box.json.previous ]]; then
    mv -f .private/sing-box.json.previous .private/sing-box.json
    "${sudo_cmd[@]}" systemctl restart tmm-proxy.service || true
  fi
  echo 'No proxy node passed the cloud egress check; deployment stopped.' >&2
  exit 1
fi
if [[ ! -f .env ]]; then cp .env.example .env; fi
env_next=$(mktemp "$remote_dir/.env.XXXXXX")
grep -v '^GECKOTERMINAL_PROXY_URLS=' .env >"$env_next" || true
joined=$(IFS=,; echo "${proxy_urls[*]}")
printf 'GECKOTERMINAL_PROXY_URLS=%s\n' "$joined" >>"$env_next"
chmod 600 "$env_next"
mv -f "$env_next" .env
printf 'Proxy endpoints: %s configured, %s healthy unique exits on cloud\n' "$proxy_count" "${#proxy_urls[@]}"
REMOTE
fi

ssh "${ssh_opts[@]}" "$target" bash -s -- "$remote_dir" "$proxy_count" <<'REMOTE'
set -euo pipefail
if [[ -f "$HOME/.cargo/env" ]]; then source "$HOME/.cargo/env"; fi
remote_dir=$1
proxy_count=$2
cd "$remote_dir"
if [[ ! -f .env ]]; then cp .env.example .env; fi
chmod 600 .env
mkdir -p data
cargo build --release --locked --jobs 1 --manifest-path rust-dashboard/Cargo.toml

sudo_cmd=()
if [[ $(id -u) != 0 ]]; then sudo_cmd=(sudo -n); fi
service_user=$(id -un)
proxy_dependency=
if (( proxy_count )); then proxy_dependency=tmm-proxy.service; fi
unit_file=$(mktemp)
trap 'rm -f "$unit_file"' EXIT
cat >"$unit_file" <<UNIT
[Unit]
Description=Token Meme Monitor
Wants=network-online.target $proxy_dependency
After=network-online.target $proxy_dependency

[Service]
Type=simple
User=$service_user
WorkingDirectory=$remote_dir
EnvironmentFile=$remote_dir/.env
ExecStart=$remote_dir/tmm all
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

if [[ -f tmm ]]; then cp -p tmm tmm.previous; fi
install -m 755 rust-dashboard/target/release/tmm tmm.next
mv -f tmm.next tmm
"${sudo_cmd[@]}" install -m 644 "$unit_file" /etc/systemd/system/tmm.service
"${sudo_cmd[@]}" systemctl daemon-reload
"${sudo_cmd[@]}" systemctl enable tmm.service
if ! "${sudo_cmd[@]}" systemctl restart tmm.service || { sleep 2; ! "${sudo_cmd[@]}" systemctl is-active --quiet tmm.service; }; then
  "${sudo_cmd[@]}" systemctl status --no-pager tmm.service || true
  if [[ -f tmm.previous ]]; then
    mv -f tmm.previous tmm
    "${sudo_cmd[@]}" systemctl restart tmm.service || true
  fi
  exit 1
fi
dashboard_port=$(sed -n 's/^DASHBOARD_PORT=\([0-9]*\)$/\1/p' .env | tail -1)
dashboard_port=${dashboard_port:-8501}
healthy=0
for attempt in 1 2 3 4 5; do
  if curl -fsS --max-time 8 -o /dev/null "http://127.0.0.1:$dashboard_port/api/overview?limit=1"; then
    healthy=1
    break
  fi
  sleep 2
done
if (( ! healthy )); then
  echo 'Dashboard health check failed after deployment.' >&2
  if [[ -f tmm.previous ]]; then
    mv -f tmm.previous tmm
    "${sudo_cmd[@]}" systemctl restart tmm.service || true
  fi
  exit 1
fi
"${sudo_cmd[@]}" systemctl status --no-pager --lines=5 tmm.service
REMOTE

echo "Deployed to $target:$remote_dir"
echo "Logs: ssh -p $port $target 'sudo journalctl -u tmm.service -f -n 100'"

if (( observe_minutes )); then
  ssh "${ssh_opts[@]}" "$target" bash -s -- "$observe_minutes" "$remote_dir" "$proxy_count" <<'REMOTE'
set -euo pipefail
minutes=$1
remote_dir=$2
proxy_count=$3
issues=0
dashboard_port=$(sed -n 's/^DASHBOARD_PORT=\([0-9]*\)$/\1/p' "$remote_dir/.env" | tail -1)
dashboard_port=${dashboard_port:-8501}
echo "Observing services for $minutes minutes (summary every 5 minutes)..."
for ((minute=1; minute<=minutes; minute++)); do
  sleep 60
  dashboard=$(systemctl is-active tmm.service || true)
  proxy=unused
  if (( proxy_count )); then proxy=$(systemctl is-active tmm-proxy.service || true); fi
  if (( minute % 5 == 0 || minute == minutes )) || [[ $dashboard != active ]] || { (( proxy_count )) && [[ $proxy != active ]]; }; then
    http=$(curl -sS --max-time 8 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$dashboard_port/api/overview?limit=1" || true)
    proxy_memory=0
    if (( proxy_count )); then proxy_memory=$(systemctl show -p MemoryCurrent --value tmm-proxy.service); fi
    if [[ $proxy_memory =~ ^[0-9]+$ ]]; then proxy_memory=$((proxy_memory / 1024 / 1024)); else proxy_memory='?'; fi
    recent=$(sudo -n journalctl -u tmm.service --since '5 minutes ago' --no-pager -o cat)
    counts=$(printf '%s\n' "$recent" | awk '
      /"event":"whale_proxy_request_completed"/ {proxy_ok++; if (match($0, /"proxy_index":[0-9]+/)) used[substr($0, RSTART, RLENGTH)]=1}
      /"event":"whale_proxy_failed"/ {proxy_failed++}
      /"event":"whale_proxy_fallback_direct"/ {direct_fallback++}
      /"event":"whale_provider_rate_limited"/ {rate_limited++}
      /"event":"whale_warm_batch_completed"/ {whale_batches++}
      /"event":"oi_warm_batch_completed"/ {oi_batches++}
      /"event":"depth_warm_batch_completed"/ {depth_batches++}
      END {for (node in used) unique++; printf "proxy_ok=%d unique=%d proxy_fail=%d direct_fallback=%d 429=%d whale=%d oi=%d depth=%d", proxy_ok,unique,proxy_failed,direct_fallback,rate_limited,whale_batches,oi_batches,depth_batches}' )
    printf '%02d/%02d min: dashboard=%s proxy=%s proxy_mem=%sMiB HTTP=%s %s\n' "$minute" "$minutes" "$dashboard" "$proxy" "$proxy_memory" "$http" "$counts"
    if [[ $dashboard != active || $http != 200 ]] || { (( proxy_count )) && [[ $proxy != active ]]; }; then issues=1; fi
  fi
done
if (( issues )); then
  echo 'Observation detected a service or HTTP health issue; inspect journalctl.' >&2
  exit 1
fi
REMOTE
fi
