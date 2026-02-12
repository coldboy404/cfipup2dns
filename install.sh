#!/bin/bash
set -euo pipefail

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
PLAIN='\033[0m'

# 检查 root 权限
if [[ $EUID -ne 0 ]]; then echo -e "${RED}请使用 root 运行${PLAIN}"; exit 1; fi

PROJECT_DIR="/opt/montecarlo-ip-searcher"
REPO_URL="https://github.com/Leo-Mu/montecarlo-ip-searcher.git"
REPO_URL_PROXY="https://gh-proxy.com/https://github.com/Leo-Mu/montecarlo-ip-searcher.git"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 1. 基础环境准备
echo -e "${GREEN}[*] 1. 安装依赖...${PLAIN}"
apt-get update && apt-get install -y jq wget git curl

# 2. 安装 Go (如果不存在)
export PATH=$PATH:/usr/local/go/bin
if ! command -v go &> /dev/null; then
    echo -e "${GREEN}[*] 2. 安装 Go 1.23...${PLAIN}"
    wget -q --show-progress https://go.dev/dl/go1.23.4.linux-amd64.tar.gz -O /tmp/go.tar.gz
    tar -C /usr/local -xzf /tmp/go.tar.gz
    rm /tmp/go.tar.gz
fi

# === 配置 Go 国内代理 (关键修复) ===
go env -w GOPROXY=https://goproxy.cn,direct

# 3. 拉取源码 & 编译
echo -e "${GREEN}[*] 3. 拉取源码并编译...${PLAIN}"
if [ -d "$PROJECT_DIR/.git" ]; then
    git -C "$PROJECT_DIR" pull --rebase
else
    rm -rf "$PROJECT_DIR"
    git clone "$REPO_URL" "$PROJECT_DIR" || git clone "$REPO_URL_PROXY" "$PROJECT_DIR"
fi
cd "$PROJECT_DIR" || exit 1

go mod tidy 2>/dev/null || true
go mod edit -go=1.23
go mod edit -toolchain=none
go mod tidy

MAIN_FILE=$(find . -name "main.go" -print0 | xargs -0 grep -l "package main" | head -n 1)
BUILD_DIR=$(dirname "$MAIN_FILE")
go build -o montecarlo-ip-searcher "$BUILD_DIR"
chmod +x montecarlo-ip-searcher

if [ ! -f "./montecarlo-ip-searcher" ]; then
    echo -e "${RED}[!] 编译失败！${PLAIN}"; exit 1
fi

# 4. 下载 IP 库
echo -e "${GREEN}[*] 4. 下载 IP 库文件...${PLAIN}"
wget -O ipv4cidr.txt https://gh-proxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/master/ipv4cidr.txt
if [ ! -s ipv4cidr.txt ]; then
    echo "下载失败，写入内置 IP 段..."
    cat > ipv4cidr.txt <<END
173.245.48.0/20
103.21.244.0/22
103.22.200.0/22
103.31.4.0/22
141.101.64.0/18
108.162.192.0/18
190.93.240.0/20
188.114.96.0/20
197.234.240.0/22
198.41.128.0/17
162.158.0.0/15
104.16.0.0/12
172.64.0.0/13
131.0.72.0/22
END
fi

# 5. 生成配置文件 (仅用于存储 Token)
echo -e "${GREEN}[*] 5. 配置 Cloudflare 信息...${PLAIN}"
OLD_TOKEN=""
OLD_ZONE=""
OLD_DOMAIN=""
if [ -f "config.json" ]; then
    OLD_TOKEN=$(jq -r .cloudflare.token config.json 2>/dev/null)
    OLD_ZONE=$(jq -r .cloudflare.zone_id config.json 2>/dev/null)
    OLD_DOMAIN=$(jq -r .cloudflare.domain config.json 2>/dev/null)
fi

if [ -z "$OLD_TOKEN" ] || [ "$OLD_TOKEN" == "null" ]; then
    echo -e "请输入 Cloudflare 信息 (后续可修改 $PROJECT_DIR/config.json):"
    read -p "API Token: " CF_KEY
    read -p "Zone ID: " CF_ZONE
    read -p "域名 (如 best.example.com): " CF_DOMAIN
else
    CF_KEY=$OLD_TOKEN
    CF_ZONE=$OLD_ZONE
    CF_DOMAIN=$OLD_DOMAIN
    echo "检测到旧配置，已自动保留。"
fi

cat > config.json <<END
{
  "cloudflare": {
    "token": "$CF_KEY",
    "zone_id": "$CF_ZONE",
    "domain": "$CF_DOMAIN"
  }
}
END

# 6. 安装运行脚本
echo -e "${GREEN}[*] 6. 安装运行脚本...${PLAIN}"
install -m 755 "$SCRIPT_DIR/cfip.sh" /usr/local/bin/cfip

# 7. 添加定时任务 (每2小时)
echo -e "${GREEN}[*] 7. 设置定时任务 (每2小时)...${PLAIN}"
(crontab -l 2>/dev/null | grep -v "cfip"; echo "0 */2 * * * /bin/bash /usr/local/bin/cfip >> $PROJECT_DIR/cron.log 2>&1") | crontab -
(crontab -l 2>/dev/null | grep -v "@reboot"; echo "@reboot sleep 60 && /bin/bash /usr/local/bin/cfip >> $PROJECT_DIR/boot.log 2>&1") | crontab -

echo -e "${GREEN}=============================================${PLAIN}"
echo -e "${GREEN}   安装完成！请输入 cfip 立即运行测试。   ${PLAIN}"
echo -e "${GREEN}=============================================${PLAIN}"
