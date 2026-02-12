#!/bin/bash
set -euo pipefail

# ================= 配置区域 =================
PROJECT_DIR="${PROJECT_DIR:-/opt/montecarlo-ip-searcher}"
CONFIG_FILE="${CONFIG_FILE:-$PROJECT_DIR/config.json}"
RESULT_FILE="$PROJECT_DIR/scan_results.log"
FINAL_IPS="$PROJECT_DIR/best_ips.json"

# 优选策略（可通过环境变量覆盖）
DOWNLOAD_BYTES="${DOWNLOAD_BYTES:-5000000}"
DOWNLOAD_TIMEOUT="${DOWNLOAD_TIMEOUT:-8s}"
CONCURRENCY="${CONCURRENCY:-50}"
TOP_TEST="${TOP_TEST:-50}"
TOP_N="${TOP_N:-5}"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
PLAIN='\033[0m'

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo -e "${RED}[!] 缺少依赖: $1${PLAIN}"; exit 1; }; }
need_cmd jq
need_cmd curl

if [ ! -f "$CONFIG_FILE" ]; then
  echo -e "${RED}[!] 找不到配置文件: $CONFIG_FILE${PLAIN}"
  exit 1
fi

CF_TOKEN=$(jq -r .cloudflare.token "$CONFIG_FILE")
CF_ZONE_ID=$(jq -r .cloudflare.zone_id "$CONFIG_FILE")
CF_DOMAIN=$(jq -r .cloudflare.domain "$CONFIG_FILE")

if [ -z "$CF_TOKEN" ] || [ "$CF_TOKEN" == "null" ]; then
  echo -e "${RED}[!] 错误：配置文件中未找到 Cloudflare Token${PLAIN}"
  exit 1
fi

if [ ! -x "$PROJECT_DIR/montecarlo-ip-searcher" ]; then
  echo -e "${RED}[!] 未找到 montecarlo-ip-searcher 可执行文件${PLAIN}"
  exit 1
fi

mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"
rm -f "$RESULT_FILE"

# 1. 运行扫描
echo -e "${GREEN}==============================================${PLAIN}"
echo -e "${GREEN}   开始优选：目标最快速度 (Top ${TOP_N})   ${PLAIN}"
echo -e "${GREEN}==============================================${PLAIN}"

echo -e "${YELLOW}[*] 正在进行测速扫描...${PLAIN}"
./montecarlo-ip-searcher \
  -cidr-file ipv4cidr.txt \
  -download-bytes "$DOWNLOAD_BYTES" \
  -download-timeout "$DOWNLOAD_TIMEOUT" \
  -download-top "$TOP_TEST" \
  -top "$TOP_TEST" \
  -concurrency "$CONCURRENCY" \
  -out jsonl \
  > "$RESULT_FILE" 2>&1

if [ ! -s "$RESULT_FILE" ]; then
  echo -e "${RED}[!] 扫描结果为空${PLAIN}"
  exit 1
fi

# 2. 数据处理：按 download_mbps 从大到小排序，取前 TOP_N
echo -e "${YELLOW}[*] 正在筛选速度最快的 ${TOP_N} 个 IP...${PLAIN}"

grep "download_mbps" "$RESULT_FILE" | jq -s --argjson top "$TOP_N" '
  map(select(.download_mbps > 0)) |
  unique_by(.ip) |
  sort_by(.download_mbps) | reverse |
  .[0:$top] |
  {result: .}
' > "$FINAL_IPS"

IP_COUNT=$(jq '.result | length' "$FINAL_IPS")
if [ "$IP_COUNT" -eq 0 ]; then
  echo -e "${RED}[!] 未找到有效 IP！可能是扫描时间太短或网络波动。${PLAIN}"
  exit 1
fi

echo -e "${GREEN}[+] 筛选成功！速度最快的 $IP_COUNT 个 IP：${PLAIN}"
printf "%-18s %-15s %-10s\n" "IP地址" "下载速度" "地区"
echo "------------------------------------------------"
jq -r '.result[] | "\(.ip)\t\(.download_mbps) Mbps\t\(.trace.colo)"' "$FINAL_IPS" | \
  awk -F'\t' '{printf "%-18s %-15s %-10s\n", $1, $2, $3}'

# 3. 上传到 Cloudflare
cf_api() {
  local method="$1"; shift
  local url="$1"; shift
  curl -s -X "$method" "$url" \
    -H "Authorization: Bearer $CF_TOKEN" \
    -H "Content-Type: application/json" \
    "$@"
}

echo -e "${YELLOW}[*] 正在更新 Cloudflare DNS 记录...${PLAIN}"

EXISTING_RECORDS=$(cf_api GET "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records?type=A&name=$CF_DOMAIN")

# 删除旧记录
jq -r '.result[].id' <<< "$EXISTING_RECORDS" | while read -r record_id; do
  if [ -n "$record_id" ] && [ "$record_id" != "null" ]; then
    cf_api DELETE "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records/$record_id" >/dev/null
  fi
done

# 添加新记录
jq -r '.result[].ip' "$FINAL_IPS" | while read -r ip; do
  cf_api POST "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records" \
    --data '{"type":"A","name":"'"$CF_DOMAIN"'","content":"'"$ip"'","ttl":60,"proxied":false}' \
    >/dev/null
done

echo -e "${GREEN}==============================================${PLAIN}"
echo -e "${GREEN}   搞定！速度最快的 ${TOP_N} 个 IP 已解析到域名。   ${PLAIN}"
echo -e "${GREEN}==============================================${PLAIN}"
