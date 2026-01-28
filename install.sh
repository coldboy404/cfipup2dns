cat > install.sh <<'EOF'
#!/bin/bash

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
PLAIN='\033[0m'

if [[ $EUID -ne 0 ]]; then echo -e "${RED}请使用 root 运行${PLAIN}"; exit 1; fi

# 1. 基础环境准备
echo -e "${GREEN}[*] 1. 清理环境 & 安装依赖...${PLAIN}"
rm -rf /opt/montecarlo-ip-searcher
rm -rf /usr/local/go
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
export PATH=$PATH:/usr/local/go/bin
go env -w GOPROXY=https://goproxy.cn,direct

# 3. 拉取源码 & 编译
echo -e "${GREEN}[*] 3. 拉取源码并编译...${PLAIN}"
PROJECT_DIR="/opt/montecarlo-ip-searcher"
git clone https://github.com/Leo-Mu/montecarlo-ip-searcher.git $PROJECT_DIR
cd $PROJECT_DIR || exit 1

# 修复版本号并自动寻找入口
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

# 4. 下载 IP 库 (CIDR) - [这里是你刚才缺失的一步]
echo -e "${GREEN}[*] 4. 下载 IP 库文件...${PLAIN}"
wget -O ipv4cidr.txt https://gh-proxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/master/ipv4cidr.txt
if [ ! -s ipv4cidr.txt ]; then
    # 如果下载失败，手动写入 Cloudflare IP 段作为保底
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

# 5. 生成配置文件
echo -e "${GREEN}[*] 5. 生成配置文件...${PLAIN}"
# 如果已存在旧配置，先读取旧配置的值
OLD_TOKEN=""
OLD_ZONE=""
OLD_DOMAIN=""
if [ -f "config.json" ]; then
    OLD_TOKEN=$(jq -r .cloudflare.token config.json 2>/dev/null)
    OLD_ZONE=$(jq -r .cloudflare.zone_id config.json 2>/dev/null)
    OLD_DOMAIN=$(jq -r .cloudflare.domain config.json 2>/dev/null)
fi

# 如果没有旧配置，询问用户
if [ -z "$OLD_TOKEN" ] || [ "$OLD_TOKEN" == "null" ]; then
    echo -e "${YELLOW}请输入 Cloudflare 信息 (后续可修改 /opt/montecarlo-ip-searcher/config.json):${PLAIN}"
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
  "max_thread": 20,
  "max_duration": 60,
  "min_speed": 0,
  "source_url": "https://gh-proxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/master/source.txt",
  "cloudflare": {
    "enabled": false,
    "token": "$CF_KEY",
    "zone_id": "$CF_ZONE",
    "domain": "$CF_DOMAIN",
    "record_type": "A",
    "proxied": false
  }
}
END

# 6. 生成核心运行脚本 (cfip) - [这里恢复了 Top 5 逻辑]
echo -e "${GREEN}[*] 6. 生成智能运行脚本 (cfip)...${PLAIN}"
cat > /usr/local/bin/cfip <<'SCRIPT'
#!/bin/bash
PROJECT_DIR="/opt/montecarlo-ip-searcher"
RESULT_FILE="$PROJECT_DIR/scan_results.log"
FINAL_IPS="$PROJECT_DIR/best_5.json"
CONFIG_FILE="$PROJECT_DIR/config.json"

# 读取配置
CF_TOKEN=$(jq -r .cloudflare.token $CONFIG_FILE)
CF_ZONE_ID=$(jq -r .cloudflare.zone_id $CONFIG_FILE)
CF_DOMAIN=$(jq -r .cloudflare.domain $CONFIG_FILE)

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
PLAIN='\033[0m'

if [ "$CF_TOKEN" == "null" ] || [ -z "$CF_TOKEN" ]; then
    echo -e "${RED}[!] 错误：请先编辑 config.json 配置 Cloudflare Token${PLAIN}"
    exit 1
fi

echo -e "${GREEN}=== 开始优选: 速度最快 (Top 5) ===${PLAIN}"
cd $PROJECT_DIR
rm -f $RESULT_FILE

# 1. 运行扫描 (指定 ipv4cidr.txt)
./montecarlo-ip-searcher --cidr-file ipv4cidr.txt > $RESULT_FILE 2>&1

# 2. 筛选 Top 5
grep "^{" $RESULT_FILE | jq -s '
  map(select(.ok == true and .download_mbps > 0)) | 
  sort_by(.download_mbps) | reverse | 
  unique_by(.ip) | 
  .[0:5] | 
  {result: .}
' > $FINAL_IPS

COUNT=$(jq '.result | length' $FINAL_IPS)
if [ "$COUNT" -eq "0" ]; then echo -e "${RED}[!] 未找到有效 IP${PLAIN}"; exit 1; fi

echo -e "${GREEN}[+] 筛选出 $COUNT 个最佳 IP:${PLAIN}"
jq -r '.result[] | "\(.ip) \t \(.download_mbps) Mbps"' $FINAL_IPS

# 3. 更新 Cloudflare
echo -e "${GREEN}[*] 更新 DNS 记录...${PLAIN}"
# 删除旧记录
RECS=$(curl -s -H "Authorization: Bearer $CF_TOKEN" "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records?type=A&name=$CF_DOMAIN")
echo "$RECS" | jq -r '.result[].id' | while read id; do
    curl -s -X DELETE -H "Authorization: Bearer $CF_TOKEN" "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records/$id" >/dev/null
done
# 添加新记录
jq -r '.result[].ip' $FINAL_IPS | while read ip; do
    curl -s -X POST -H "Authorization: Bearer $CF_TOKEN" -H "Content-Type: application/json" \
    "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records" \
    --data '{"type":"A","name":"'"$CF_DOMAIN"'","content":"'"$ip"'","ttl":60,"proxied":false}' >/dev/null
done
echo -e "${GREEN}=== 完成 ===${PLAIN}"
SCRIPT
chmod +x /usr/local/bin/cfip

# 7. 添加定时任务
echo -e "${GREEN}[*] 7. 设置定时任务 (每两小时)...${PLAIN}"
(crontab -l 2>/dev/null | grep -v "cfip"; echo "0 */2 * * * /bin/bash /usr/local/bin/cfip >> /opt/montecarlo-ip-searcher/cron.log 2>&1") | crontab -
(crontab -l 2>/dev/null | grep -v "@reboot"; echo "@reboot sleep 60 && /bin/bash /usr/local/bin/cfip >> /opt/montecarlo-ip-searcher/boot.log 2>&1") | crontab -

echo -e "${GREEN}=========================================${PLAIN}"
echo -e "${GREEN}   安装全部完成！请直接输入 cfip 运行   ${PLAIN}"
echo -e "${GREEN}=========================================${PLAIN}"
EOF
