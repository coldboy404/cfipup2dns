#!/bin/bash
set -euo pipefail

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
PLAIN='\033[0m'

# 检查 root 权限
if [[ $EUID -ne 0 ]]; then
  echo -e "${RED}请使用 root 运行${PLAIN}"
  exit 1
fi

PROJECT_DIR="/opt/montecarlo-ip-searcher"
REPO_URL="https://github.com/Leo-Mu/montecarlo-ip-searcher.git"
REPO_URL_PROXY="https://gh-proxy.com/https://github.com/Leo-Mu/montecarlo-ip-searcher.git"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 1. 基础环境准备
echo -e "${GREEN}[*] 1. 安装依赖...${PLAIN}"
apt-get update
apt-get install -y jq wget git curl ca-certificates

# 2. 安装 Go (如果不存在)
export PATH=$PATH:/usr/local/go/bin
if ! command -v go &>/dev/null; then
  echo -e "${GREEN}[*] 2. 安装 Go ...${PLAIN}"
  GO_TARBALL="go1.25.1.linux-amd64.tar.gz"
  wget -q --show-progress "https://go.dev/dl/${GO_TARBALL}" -O /tmp/go.tar.gz
  tar -C /usr/local -xzf /tmp/go.tar.gz
  rm -f /tmp/go.tar.gz
fi

go version || true

# Go 代理（加速下载模块）
go env -w GOPROXY=https://goproxy.cn,direct || true

# 3. 拉取源码 & 编译
echo -e "${GREEN}[*] 3. 拉取源码并编译 montecarlo-ip-searcher...${PLAIN}"
if [ -d "$PROJECT_DIR/.git" ]; then
  git -C "$PROJECT_DIR" fetch --all --tags
  git -C "$PROJECT_DIR" reset --hard origin/main
else
  rm -rf "$PROJECT_DIR"
  git clone "$REPO_URL" "$PROJECT_DIR" || git clone "$REPO_URL_PROXY" "$PROJECT_DIR"
fi

cd "$PROJECT_DIR"
go mod tidy

# 优先按上游推荐入口构建，失败则自动回退
if ! go build -o montecarlo-ip-searcher ./cmd/mcis; then
  echo -e "${YELLOW}[!] ./cmd/mcis 构建失败，尝试自动发现 main 包...${PLAIN}"
  MAIN_FILE=$(find . -name "main.go" -print0 | xargs -0 grep -l "package main" | head -n 1)
  [ -z "$MAIN_FILE" ] && { echo -e "${RED}[!] 未找到 main.go${PLAIN}"; exit 1; }
  BUILD_DIR=$(dirname "$MAIN_FILE")
  go build -o montecarlo-ip-searcher "$BUILD_DIR"
fi
chmod +x montecarlo-ip-searcher

if [ ! -x "./montecarlo-ip-searcher" ]; then
  echo -e "${RED}[!] 编译失败！${PLAIN}"
  exit 1
fi

# 4. 下载 IP 库（IPv4+IPv6）
echo -e "${GREEN}[*] 4. 下载 IP 库文件...${PLAIN}"
wget -q -O ipv4cidr.txt https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv4cidr.txt \
  || wget -q -O ipv4cidr.txt https://gh-proxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv4cidr.txt

wget -q -O ipv6cidr.txt https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv6cidr.txt \
  || wget -q -O ipv6cidr.txt https://gh-proxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv6cidr.txt

if [ ! -s ipv4cidr.txt ]; then
  echo -e "${RED}[!] ipv4cidr.txt 下载失败${PLAIN}"
  exit 1
fi
if [ ! -s ipv6cidr.txt ]; then
  echo -e "${YELLOW}[!] ipv6cidr.txt 下载失败，IPv6 模式将不可用${PLAIN}"
fi

# 5. 生成配置文件（保留旧配置）
echo -e "${GREEN}[*] 5. 配置 Cloudflare 信息...${PLAIN}"
OLD_TOKEN=""
OLD_ZONE=""
OLD_DOMAIN=""
OLD_TTL="60"
OLD_PROXIED="false"
if [ -f "config.json" ]; then
  OLD_TOKEN=$(jq -r '.cloudflare.token // empty' config.json 2>/dev/null || true)
  OLD_ZONE=$(jq -r '.cloudflare.zone_id // empty' config.json 2>/dev/null || true)
  OLD_DOMAIN=$(jq -r '.cloudflare.domain // empty' config.json 2>/dev/null || true)
  OLD_TTL=$(jq -r '.cloudflare.ttl // 60' config.json 2>/dev/null || echo 60)
  OLD_PROXIED=$(jq -r '.cloudflare.proxied // false' config.json 2>/dev/null || echo false)
fi

if [ -z "$OLD_TOKEN" ] || [ -z "$OLD_ZONE" ] || [ -z "$OLD_DOMAIN" ]; then
  echo -e "请输入 Cloudflare 信息 (后续可修改 $PROJECT_DIR/config.json):"
  read -rp "API Token: " CF_KEY
  read -rp "Zone ID: " CF_ZONE
  read -rp "域名 (如 best.example.com): " CF_DOMAIN
  read -rp "TTL(默认 60): " CF_TTL
  read -rp "是否开启代理 proxied? (true/false，默认 false): " CF_PROXIED
  CF_TTL=${CF_TTL:-60}
  CF_PROXIED=${CF_PROXIED:-false}
else
  CF_KEY=$OLD_TOKEN
  CF_ZONE=$OLD_ZONE
  CF_DOMAIN=$OLD_DOMAIN
  CF_TTL=$OLD_TTL
  CF_PROXIED=$OLD_PROXIED
  echo "检测到旧配置，已自动保留。"
fi

cat >config.json <<END
{
  "cloudflare": {
    "token": "$CF_KEY",
    "zone_id": "$CF_ZONE",
    "domain": "$CF_DOMAIN",
    "ttl": $CF_TTL,
    "proxied": $CF_PROXIED
  }
}
END

# 6. 安装运行脚本
echo -e "${GREEN}[*] 6. 安装运行脚本...${PLAIN}"
install -m 755 "$SCRIPT_DIR/cfip.sh" /usr/local/bin/cfip
install -m 755 "$SCRIPT_DIR/menu.sh" /usr/local/bin/cfip-menu

# 7. 添加定时任务（每2小时）
echo -e "${GREEN}[*] 7. 设置定时任务 (每2小时 + 开机自启)...${PLAIN}"
CRON_TMP=$(mktemp)
crontab -l 2>/dev/null | grep -v "cfip" > "$CRON_TMP" || true
echo "0 */2 * * * /bin/bash /usr/local/bin/cfip >> $PROJECT_DIR/cron.log 2>&1" >> "$CRON_TMP"
echo "@reboot sleep 60 && /bin/bash /usr/local/bin/cfip >> $PROJECT_DIR/boot.log 2>&1" >> "$CRON_TMP"
crontab "$CRON_TMP"
rm -f "$CRON_TMP"

echo -e "${GREEN}[*] 8. 输入快捷命令 cfip 可立即运行一次${PLAIN}"

echo -e "${GREEN}=============================================${PLAIN}"
echo -e "${GREEN}   安装完成！请输入 cfip 立即运行测试。   ${PLAIN}"
echo -e "${GREEN}=============================================${PLAIN}"
