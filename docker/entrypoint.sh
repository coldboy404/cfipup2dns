#!/bin/sh
set -eu

PROJECT_DIR="${PROJECT_DIR:-/data/project}"
CONFIG_FILE="${CONFIG_FILE:-$PROJECT_DIR/config.json}"
CRON_FILE="${CRON_FILE:-/data/cron/cfip.cron}"
LOG_DIR="$(dirname "${LOG_FILE:-/data/logs/cron.log}")"
PORT="${PORT:-9527}"

mkdir -p "$PROJECT_DIR" "$(dirname "$CRON_FILE")" "$LOG_DIR"

if ! /opt/cfipup2dns/docker/init-mcis.sh >> /data/logs/init-mcis.log 2>&1; then
  printf '%s\n' "[警告] mcis 初始化失败，Web 面板仍会启动；请查看 /data/logs/init-mcis.log" >> /data/logs/init-mcis.log
fi

if [ ! -f "$CONFIG_FILE" ]; then
  cp /opt/cfipup2dns/config.example.json "$CONFIG_FILE"
fi

if [ ! -f "$CRON_FILE" ]; then
  cat > "$CRON_FILE" <<'CRON'
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 */2 * * * IP_MODE=both TOP_N=5 /opt/cfipup2dns/cfip.sh >> /data/logs/cron.log 2>&1
@reboot sleep 30 && IP_MODE=both TOP_N=5 /opt/cfipup2dns/cfip.sh >> /data/logs/boot.log 2>&1
CRON
fi

exec python3 /opt/cfipup2dns/web/app.py
