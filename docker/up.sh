#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export DOCKER_CLIENT_TIMEOUT="${DOCKER_CLIENT_TIMEOUT:-600}"
export COMPOSE_HTTP_TIMEOUT="${COMPOSE_HTTP_TIMEOUT:-600}"
export BUILDKIT_PROGRESS="plain"

if [[ -n "${GH_PROXY:-}" ]]; then
  echo "[selected] GH_PROXY=${GH_PROXY}"
else
  echo "[selected] GH_PROXY=<direct>"
fi

docker compose build --no-cache

docker compose up -d

echo "Done. WebUI: http://<server-ip>:9527"
