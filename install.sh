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
TMP_ASSETS_DIR="/tmp/cfipup2dns-assets"
RAW_BASE="https://raw.githubusercontent.com/coldboy404/cfipup2dns/main"
RAW_BASE_PROXY="https://gh-proxy.com/https://raw.githubusercontent.com/coldboy404/cfipup2dns/main"

mkdir -p "$TMP_ASSETS_DIR" "$PROJECT_DIR"

fetch_asset() {
  local name="$1"
  local dst="$TMP_ASSETS_DIR/$name"
  curl -fsSL "$RAW_BASE/$name" -o "$dst" \
    || curl -fsSL "$RAW_BASE_PROXY/$name" -o "$dst"
}

download_mcis_release() {
  local arch rel_arch tag url url_proxy tgz
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) rel_arch="amd64" ;;
    aarch64|arm64) rel_arch="arm64" ;;
    *)
      echo -e "${RED}[!] 不支持的架构: $arch（当前仅支持 amd64/arm64）${PLAIN}"
      exit 1
      ;;
  esac

  tag="${MCIS_TAG:-v0.2.3}"
  tgz="$PROJECT_DIR/mcis-release.tgz"
  url="https://github.com/Leo-Mu/montecarlo-ip-searcher/releases/download/${tag}/mcis-${tag}-linux-${rel_arch}.tar.gz"
  url_proxy="https://gh-proxy.com/${url}"

  echo -e "${YELLOW}[*] 下载 mcis 预编译包: ${tag} (${rel_arch})${PLAIN}"
  curl -fL "$url" -o "$tgz" || curl -fL "$url_proxy" -o "$tgz"

  tar -xzf "$tgz" -C "$PROJECT_DIR"
  rm -f "$tgz"

  if [[ ! -x "$PROJECT_DIR/mcis" ]]; then
    echo -e "${RED}[!] mcis 解压后不可执行${PLAIN}"
    exit 1
  fi

  mv -f "$PROJECT_DIR/mcis" "$PROJECT_DIR/montecarlo-ip-searcher"
  chmod +x "$PROJECT_DIR/montecarlo-ip-searcher"
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

echo -e "${GREEN}[*] 1/6 安装基础依赖...${PLAIN}"
apt-get update
apt-get install -y jq wget git curl ca-certificates tar

echo -e "${GREEN}[*] 2/6 准备运行脚本资源...${PLAIN}"
fetch_asset cfip.sh
fetch_asset menu.sh
chmod +x "$TMP_ASSETS_DIR/cfip.sh" "$TMP_ASSETS_DIR/menu.sh"

echo -e "${GREEN}[*] 3/6 安装 mcis（使用预编译二进制，无需本机 Go）...${PLAIN}"
download_mcis_release

echo -e "${GREEN}[*] 4/6 确认 CIDR 文件...${PLAIN}"
[[ -s "$PROJECT_DIR/ipv4cidr.txt" ]] || {
  curl -fsSL https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv4cidr.txt -o "$PROJECT_DIR/ipv4cidr.txt" \
    || curl -fsSL https://gh-proxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv4cidr.txt -o "$PROJECT_DIR/ipv4cidr.txt"
}
[[ -s "$PROJECT_DIR/ipv6cidr.txt" ]] || {
  curl -fsSL https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv6cidr.txt -o "$PROJECT_DIR/ipv6cidr.txt" \
    || curl -fsSL https://gh-proxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv6cidr.txt -o "$PROJECT_DIR/ipv6cidr.txt"
}

echo -e "${GREEN}[*] 5/6 处理配置...${PLAIN}"
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

echo -e "${GREEN}[*] 6/6 安装命令与定时任务...${PLAIN}"
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
