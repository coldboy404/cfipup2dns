#!/bin/bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
PLAIN='\033[0m'

if [[ ${EUID:-0} -ne 0 ]]; then
  echo -e "${RED}[!] 请使用 root 运行 install.sh${PLAIN}"
  exit 1
fi

PROJECT_DIR="/opt/montecarlo-ip-searcher"
UPSTREAM_REPO="https://github.com/Leo-Mu/montecarlo-ip-searcher.git"
UPSTREAM_REPO_PROXY="https://gh-proxy.com/https://github.com/Leo-Mu/montecarlo-ip-searcher.git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
TMP_ASSETS_DIR="/tmp/cfipup2dns-assets"
RAW_BASE="https://raw.githubusercontent.com/coldboy404/cfipup2dns/main"
RAW_BASE_PROXY="https://gh-proxy.com/https://raw.githubusercontent.com/coldboy404/cfipup2dns/main"

mkdir -p "$TMP_ASSETS_DIR"

fetch_asset() {
  local name="$1"
  local dst="$TMP_ASSETS_DIR/$name"

  if [[ -f "$SCRIPT_DIR/$name" ]]; then
    cp -f "$SCRIPT_DIR/$name" "$dst"
    return 0
  fi

  curl -fsSL "$RAW_BASE/$name" -o "$dst" \
    || curl -fsSL "$RAW_BASE_PROXY/$name" -o "$dst"
}

write_config() {
  local cfg="$1" token="$2" zone="$3" domain="$4" ttl="$5" proxied="$6"
  cat > "$cfg" <<EOF
{
  "cloudflare": {
    "token": "$token",
    "zone_id": "$zone",
    "domain": "$domain",
    "ttl": $ttl,
    "proxied": $proxied
  }
}
EOF
}

echo -e "${GREEN}[*] 1/7 安装基础依赖...${PLAIN}"
apt-get update
apt-get install -y jq wget git curl ca-certificates

echo -e "${GREEN}[*] 2/7 准备运行脚本资源...${PLAIN}"
fetch_asset cfip.sh
fetch_asset menu.sh
chmod +x "$TMP_ASSETS_DIR/cfip.sh" "$TMP_ASSETS_DIR/menu.sh"

echo -e "${GREEN}[*] 3/7 安装 Go（若缺失）...${PLAIN}"
export PATH="$PATH:/usr/local/go/bin"
if ! command -v go >/dev/null 2>&1; then
  GO_TARBALL="go1.25.1.linux-amd64.tar.gz"
  wget -q --show-progress "https://go.dev/dl/${GO_TARBALL}" -O /tmp/go.tar.gz
  tar -C /usr/local -xzf /tmp/go.tar.gz
  rm -f /tmp/go.tar.gz
fi

go env -w GOPROXY=https://goproxy.cn,direct || true
go version || true

echo -e "${GREEN}[*] 4/7 拉取并编译 montecarlo-ip-searcher...${PLAIN}"
if [[ -d "$PROJECT_DIR/.git" ]]; then
  git -C "$PROJECT_DIR" fetch --all --tags
  git -C "$PROJECT_DIR" reset --hard origin/main
else
  rm -rf "$PROJECT_DIR"
  git clone "$UPSTREAM_REPO" "$PROJECT_DIR" \
    || git clone "$UPSTREAM_REPO_PROXY" "$PROJECT_DIR"
fi

cd "$PROJECT_DIR"
go mod tidy
if ! go build -o montecarlo-ip-searcher ./cmd/mcis; then
  MAIN_FILE=$(find . -name main.go -print0 | xargs -0 grep -l "package main" | head -n 1)
  [[ -z "$MAIN_FILE" ]] && { echo -e "${RED}[!] 构建失败：未找到 main.go${PLAIN}"; exit 1; }
  go build -o montecarlo-ip-searcher "$(dirname "$MAIN_FILE")"
fi
chmod +x montecarlo-ip-searcher

echo -e "${GREEN}[*] 5/7 下载 IPv4/IPv6 CIDR...${PLAIN}"
curl -fsSL https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv4cidr.txt -o ipv4cidr.txt \
  || curl -fsSL https://gh-proxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv4cidr.txt -o ipv4cidr.txt

curl -fsSL https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv6cidr.txt -o ipv6cidr.txt \
  || curl -fsSL https://gh-proxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv6cidr.txt -o ipv6cidr.txt

echo -e "${GREEN}[*] 6/7 处理配置...${PLAIN}"
CONFIG_FILE="$PROJECT_DIR/config.json"
OLD_TOKEN=""; OLD_ZONE=""; OLD_DOMAIN=""; OLD_TTL="60"; OLD_PROXIED="false"
if [[ -f "$CONFIG_FILE" ]]; then
  OLD_TOKEN=$(jq -r '.cloudflare.token // ""' "$CONFIG_FILE" 2>/dev/null || true)
  OLD_ZONE=$(jq -r '.cloudflare.zone_id // ""' "$CONFIG_FILE" 2>/dev/null || true)
  OLD_DOMAIN=$(jq -r '.cloudflare.domain // ""' "$CONFIG_FILE" 2>/dev/null || true)
  OLD_TTL=$(jq -r '.cloudflare.ttl // 60' "$CONFIG_FILE" 2>/dev/null || echo 60)
  OLD_PROXIED=$(jq -r '.cloudflare.proxied // false' "$CONFIG_FILE" 2>/dev/null || echo false)
fi

CF_KEY="${CF_TOKEN:-$OLD_TOKEN}"
CF_ZONE="${CF_ZONE_ID:-$OLD_ZONE}"
CF_DOMAIN="${CF_DOMAIN:-$OLD_DOMAIN}"
CF_TTL="${CF_TTL:-$OLD_TTL}"
CF_PROXIED="${CF_PROXIED:-$OLD_PROXIED}"

if [[ -n "$CF_KEY" && -n "$CF_ZONE" && -n "$CF_DOMAIN" ]]; then
  write_config "$CONFIG_FILE" "$CF_KEY" "$CF_ZONE" "$CF_DOMAIN" "$CF_TTL" "$CF_PROXIED"
  echo -e "${GREEN}[+] 已写入配置: $CONFIG_FILE${PLAIN}"
else
  echo -e "${YELLOW}[!] 未检测到完整 Cloudflare 配置，已跳过配置写入。${PLAIN}"
  echo -e "${YELLOW}    可在菜单中选择“2) 修改 Cloudflare 配置”后再运行。${PLAIN}"
fi

echo -e "${GREEN}[*] 7/7 安装命令与定时任务...${PLAIN}"
install -m 755 "$TMP_ASSETS_DIR/cfip.sh" /usr/local/bin/cfip-run
install -m 755 "$TMP_ASSETS_DIR/menu.sh" /usr/local/bin/cfip
install -m 755 "$TMP_ASSETS_DIR/menu.sh" /usr/local/bin/cfip-menu

CRON_TMP=$(mktemp)
crontab -l 2>/dev/null | grep -v "cfip-run" > "$CRON_TMP" || true
echo "0 */2 * * * /bin/bash /usr/local/bin/cfip-run >> $PROJECT_DIR/cron.log 2>&1" >> "$CRON_TMP"
echo "@reboot sleep 60 && /bin/bash /usr/local/bin/cfip-run >> $PROJECT_DIR/boot.log 2>&1" >> "$CRON_TMP"
crontab "$CRON_TMP"
rm -f "$CRON_TMP"

echo -e "${GREEN}=============================================${PLAIN}"
echo -e "${GREEN}安装完成！${PLAIN}"
echo -e "${GREEN}- 直接优选: cfip-run${PLAIN}"
echo -e "${GREEN}- 菜单管理: cfip${PLAIN}"
echo -e "${GREEN}=============================================${PLAIN}"
