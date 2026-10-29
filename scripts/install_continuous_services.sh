#!/usr/bin/env bash
set -euo pipefail

BASE=${TMM_BASE:-/home/ubuntu/token-meme-monitor}
SOURCE=${TMM_SOURCE:-$BASE/.source}
RUNTIME="$BASE/monitoring-runtime/continuous"
mkdir -p "$RUNTIME"
sudo -n chown -R "$(id -un):$(id -gn)" "$RUNTIME"

write_unit() {
  local name=$1 description=$2 exec_start=$3
  local unit=/tmp/tmm-$name.service
  cat >"$unit" <<UNIT
[Unit]
Description=$description
After=network-online.target tmm.service
Wants=network-online.target

[Service]
Type=simple
User=$(id -un)
WorkingDirectory=$BASE
ExecStart=$exec_start
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT
  sudo -n install -m 644 "$unit" "/etc/systemd/system/tmm-$name.service"
}

write_unit \
  structure-monitor \
  'TMM continuous structure verification every 15 minutes' \
  "/usr/bin/python3 $SOURCE/scripts/monitor_structure_continuous.py $BASE/data/monitor.db http://127.0.0.1:8501 0 continuous $RUNTIME/structure"
write_unit \
  exit-replay \
  'TMM continuous exit replay every 3 hours' \
  "/bin/bash $SOURCE/scripts/run_exit_replay_continuous.sh"
write_unit \
  early-warning \
  'TMM continuous early warning replay every 3 hours' \
  "/bin/bash $SOURCE/scripts/run_early_warning_continuous.sh"
write_unit \
  monitored-movers \
  'TMM monitored mover audit every hour' \
  "/bin/bash $SOURCE/scripts/run_monitored_mover_audit_continuous.sh"
write_unit \
  confirmation-shadow \
  'TMM live confirmation shadow logger every 5 minutes' \
  "/usr/bin/python3 $SOURCE/scripts/confirmation_shadow.py --db $BASE/data/monitor.db --api http://127.0.0.1:8501 --loop --interval 300"

sudo -n systemctl daemon-reload
for service in structure-monitor exit-replay early-warning monitored-movers confirmation-shadow; do
  sudo -n systemctl enable --now "tmm-$service.service" >/dev/null
  sudo -n systemctl restart "tmm-$service.service"
done
