#!/usr/bin/env bash
set -euo pipefail

export PROJECT_DIR="${PROJECT_DIR:-/data/project}"
export CONFIG_FILE="${CONFIG_FILE:-$PROJECT_DIR/config.json}"
export PORT="${PORT:-9527}"
export TZ="${TZ:-Asia/Shanghai}"

mkdir -p "$PROJECT_DIR" /data/logs /data/cron

if [[ ! -f "$CONFIG_FILE" ]]; then
  cp /opt/cfipup2dns/config.example.json "$CONFIG_FILE"
fi

install -m 755 /opt/cfipup2dns/cfip.sh /usr/local/bin/cfip-run

/opt/cfipup2dns/docker/init-mcis.sh

CRON_FILE="/data/cron/cfip.cron"
if [[ ! -f "$CRON_FILE" ]]; then
  cat > "$CRON_FILE" <<CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 */2 * * * IP_MODE=both TOP_N=5 /usr/local/bin/cfip-run >> /data/logs/cron.log 2>&1
@reboot sleep 30 && IP_MODE=both TOP_N=5 /usr/local/bin/cfip-run >> /data/logs/boot.log 2>&1
CRON
fi

crontab "$CRON_FILE"
cron

exec python3 /opt/cfipup2dns/web/app.py
