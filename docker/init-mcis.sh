#!/bin/sh
set -eu

PROJECT_DIR="${PROJECT_DIR:-/data/project}"
MCIS_REF="${MCIS_REF:-}"
MCIS_BIN="$PROJECT_DIR/montecarlo-ip-searcher"
MCIS_VERSION_FILE="$PROJECT_DIR/.mcis_version"
BUNDLED_DIR="/opt/cfipup2dns/bundled-mcis"
BUNDLED_VERSION="$(cat "$BUNDLED_DIR/.mcis_version")"
WANTED_VERSION="${MCIS_REF:+${MCIS_REF}+source}"
WANTED_VERSION="${WANTED_VERSION:-$BUNDLED_VERSION}"

log() {
  printf '%s\n' "$1"
}

mkdir -p "$PROJECT_DIR"

if [ -x "$MCIS_BIN" ] && [ -f "$MCIS_VERSION_FILE" ] \
  && [ "$(cat "$MCIS_VERSION_FILE" 2>/dev/null || true)" = "$WANTED_VERSION" ]; then
  log "[✓] 已使用持久化的 mcis: $WANTED_VERSION"
elif [ "$WANTED_VERSION" = "$BUNDLED_VERSION" ] && [ -x "$BUNDLED_DIR/montecarlo-ip-searcher" ]; then
  log "[*] 从镜像恢复内置 mcis，无需启动时下载或编译"
  cp "$BUNDLED_DIR/montecarlo-ip-searcher" "$MCIS_BIN"
  cp "$BUNDLED_DIR/.mcis_version" "$MCIS_VERSION_FILE"
  chmod +x "$MCIS_BIN"
else
  log "[错误] 镜像未包含 MCIS_REF=$MCIS_REF；请同时指定 MCIS_REF 与对应 MCIS_SHA256 重新构建镜像"
  exit 1
fi

export PROJECT_DIR
python3 /opt/cfipup2dns/docker/sync_cidr.py

"$MCIS_BIN" -h 2>&1 | grep -q -- '-download-mode' || {
  log "[错误] 当前 mcis 不支持 -download-mode"
  exit 1
}
