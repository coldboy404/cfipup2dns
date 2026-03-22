#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE_CANDIDATES=(
  "docker.1panel.live/library/debian:bookworm-slim"
  "docker.m.daocloud.io/library/debian:bookworm-slim"
  "dockerproxy.com/library/debian:bookworm-slim"
  "debian:bookworm-slim"
)

APT_MIRROR_CANDIDATES=(
  "mirrors.aliyun.com"
  "mirrors.tuna.tsinghua.edu.cn"
  "mirrors.ustc.edu.cn"
  "repo.huaweicloud.com"
)

GH_PROXY_CANDIDATES=(
  "https://gh-proxy.org/"
  "https://ghfast.top/"
  "https://ghproxy.net/"
  ""
)

pull_with_retry() {
  local image="$1"
  local ok=1
  for i in 1 2 3; do
    if docker pull "$image"; then
      ok=0
      break
    fi
    sleep 3
  done
  return $ok
}

pick_base_image() {
  if [[ -n "${BASE_IMAGE:-}" ]]; then
    echo "$BASE_IMAGE"
    return 0
  fi

  local img
  for img in "${IMAGE_CANDIDATES[@]}"; do
    echo "[fallback] try base image: $img"
    if pull_with_retry "$img"; then
      echo "$img"
      return 0
    fi
  done

  echo "No available base image mirror found." >&2
  exit 1
}

pick_apt_mirror() {
  if [[ -n "${APT_MIRROR:-}" ]]; then
    echo "$APT_MIRROR"
    return 0
  fi

  local host
  for host in "${APT_MIRROR_CANDIDATES[@]}"; do
    if curl -fsSLI --connect-timeout 3 --max-time 8 "https://${host}/debian/dists/bookworm/Release" >/dev/null 2>&1; then
      echo "$host"
      return 0
    fi
  done

  echo "mirrors.aliyun.com"
}

pick_gh_proxy() {
  if [[ -n "${GH_PROXY:-}" ]]; then
    echo "$GH_PROXY"
    return 0
  fi

  local p
  for p in "${GH_PROXY_CANDIDATES[@]}"; do
    if [[ -z "$p" ]]; then
      echo ""
      return 0
    fi
    if curl -fsSLI --connect-timeout 3 --max-time 8 "${p}https://raw.githubusercontent.com/" >/dev/null 2>&1; then
      echo "$p"
      return 0
    fi
  done

  echo "https://gh-proxy.org/"
}

export BASE_IMAGE="$(pick_base_image)"
export APT_MIRROR="$(pick_apt_mirror)"
export GH_PROXY="$(pick_gh_proxy)"

echo "[selected] BASE_IMAGE=$BASE_IMAGE"
echo "[selected] APT_MIRROR=$APT_MIRROR"
echo "[selected] GH_PROXY=${GH_PROXY:-<direct>}"

docker compose --progress=plain build --no-cache

docker compose up -d

echo "Done. WebUI: http://<server-ip>:9527"
