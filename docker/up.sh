#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export DOCKER_CLIENT_TIMEOUT="${DOCKER_CLIENT_TIMEOUT:-600}"
export COMPOSE_HTTP_TIMEOUT="${COMPOSE_HTTP_TIMEOUT:-600}"

if [[ "${BUILD_LOCAL:-0}" == "1" ]]; then
  echo "[selected] 本地构建镜像"
  docker compose build --pull
else
  echo "[selected] 拉取 GHCR 最新镜像"
  docker compose pull
fi

docker compose up -d --remove-orphans

echo "Done. WebUI: http://<server-ip>:${PORT:-9527}"
