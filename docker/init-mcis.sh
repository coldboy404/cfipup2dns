#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/data/project}"
GH_PROXY="${GH_PROXY:-https://gh-proxy.com/}"
MCIS_TAG="${MCIS_TAG:-v0.2.3}"

mkdir -p "$PROJECT_DIR"

arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) mcis_arch="amd64" ;;
  aarch64|arm64) mcis_arch="arm64" ;;
  *) echo "Unsupported arch: $arch"; exit 1 ;;
esac

mcis_bin="$PROJECT_DIR/montecarlo-ip-searcher"
if [[ ! -x "$mcis_bin" ]]; then
  pkg="mcis-${MCIS_TAG}-linux-${mcis_arch}.tar.gz"
  raw_url="https://github.com/Leo-Mu/montecarlo-ip-searcher/releases/download/${MCIS_TAG}/${pkg}"
  proxy_url="${GH_PROXY}${raw_url}"

  tmp_tgz="/tmp/${pkg}"
  curl -fL --connect-timeout 8 --max-time 120 "$proxy_url" -o "$tmp_tgz" \
    || curl -fL --connect-timeout 8 --max-time 120 "$raw_url" -o "$tmp_tgz"

  tar -xzf "$tmp_tgz" -C "$PROJECT_DIR"
  rm -f "$tmp_tgz"
  mv -f "$PROJECT_DIR/mcis" "$mcis_bin"
  chmod +x "$mcis_bin"
fi

if [[ ! -s "$PROJECT_DIR/ipv4cidr.txt" ]]; then
  raw4="https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv4cidr.txt"
  curl -fL "${GH_PROXY}${raw4}" -o "$PROJECT_DIR/ipv4cidr.txt" || curl -fL "$raw4" -o "$PROJECT_DIR/ipv4cidr.txt"
fi

if [[ ! -s "$PROJECT_DIR/ipv6cidr.txt" ]]; then
  raw6="https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv6cidr.txt"
  curl -fL "${GH_PROXY}${raw6}" -o "$PROJECT_DIR/ipv6cidr.txt" || curl -fL "$raw6" -o "$PROJECT_DIR/ipv6cidr.txt"
fi
