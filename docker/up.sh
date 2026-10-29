#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export DOCKER_CLIENT_TIMEOUT="${DOCKER_CLIENT_TIMEOUT:-600}"
export COMPOSE_HTTP_TIMEOUT="${COMPOSE_HTTP_TIMEOUT:-600}"

# Keep the bridge default unchanged. Host networking is opt-in on Linux and
# inherits the host's IPv6 routes; never reconfigure the Docker daemon globally.
case "${NETWORK_MODE:-bridge}" in
  bridge|host) ;;
  *) printf '%s\n' '[错误] NETWORK_MODE 只能为 bridge 或 host' >&2; exit 1 ;;
esac
compose() {
  if [[ "${NETWORK_MODE:-bridge}" == "host" ]]; then
    docker compose -f docker-compose.yml -f docker-compose.host.yml "$@"
  else
    docker compose "$@"
  fi
}
compose config --quiet

if [[ "${BUILD_LOCAL:-0}" == "1" ]]; then
  echo "[selected] 本地构建镜像"
  compose build --pull
else
  echo "[selected] 拉取 GHCR 最新镜像"
  compose pull
fi

compose up -d --remove-orphans

echo "Done. WebUI: http://<server-ip>:${PORT:-9527}"
