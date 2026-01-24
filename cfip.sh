#!/bin/bash

# ================= 配置区域 =================
PROJECT_DIR="/opt/montecarlo-ip-searcher"
RESULT_FILE="$PROJECT_DIR/scan_results.log"
FINAL_IPS="$PROJECT_DIR/best_10_speed.json"

# 读取配置
CF_TOKEN=$(jq -r .cloudflare.token $PROJECT_DIR/config.json)
CF_ZONE_ID=$(jq -r .cloudflare.zone_id $PROJECT_DIR/config.json)
CF_DOMAIN=$(jq -r .cloudflare.domain $PROJECT_DIR/config.json)
# ===========================================

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
PLAIN='\033[0m'

if [ -z "$CF_TOKEN" ] || [ "$CF_TOKEN" == "null" ]; then
    echo -e "${RED}[!] 错误：配置文件中未找到 Cloudflare Token${PLAIN}"
    exit 1
fi

echo -e "${GREEN}==============================================${PLAIN}"
echo -e "${GREEN}   开始优选：目标最快速度 (Highest Speed) - Top 10   ${PLAIN}"
echo -e "${GREEN}==============================================${PLAIN}"

cd $PROJECT_DIR
rm -f $RESULT_FILE

# 1. 运行扫描 (静默)
echo -e "${YELLOW}[*] 正在进行测速扫描 (约 60 秒)...${PLAIN}"
./montecarlo-ip-searcher --cidr-file ipv4cidr.txt > $RESULT_FILE 2>&1

# 2. 数据处理：按 download_mbps (下载速度) 从大到小排序，取前 10
echo -e "${YELLOW}[*] 正在筛选速度最快的 10 个 IP...${PLAIN}"

grep "^{" $RESULT_FILE | jq -s '
  map(select(.ok == true and .download_mbps > 0)) | 
  sort_by(.download_mbps) | reverse | 
  unique_by(.ip) | 
  .[0:10] | 
  {result: .}
' > $FINAL_IPS

IP_COUNT=$(jq '.result | length' $FINAL_IPS)

if [ "$IP_COUNT" -eq "0" ]; then
    echo -e "${RED}[!] 未找到有效 IP！可能是扫描时间太短或网络波动。${PLAIN}"
    exit 1
fi

echo -e "${GREEN}[+] 筛选成功！速度最快的 $IP_COUNT 个 IP：${PLAIN}"
printf "%-18s %-15s %-10s\n" "IP地址" "下载速度" "地区"
echo "------------------------------------------------"
jq -r '.result[] | "\(.ip) \t \(.download_mbps) Mbps \t \(.trace.colo)"' $FINAL_IPS | \
awk -F'\t' '{printf "%-18s %-15s %-10s\n", $1, $2, $3}'

# 3. 上传到 Cloudflare
echo -e "${YELLOW}[*] 正在更新 Cloudflare DNS 记录...${PLAIN}"

# 获取旧记录
EXISTING_RECORDS=$(curl -s -X GET "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records?type=A&name=$CF_DOMAIN" \
     -H "Authorization: Bearer $CF_TOKEN" \
     -H "Content-Type: application/json")

# 删除旧记录
echo "$EXISTING_RECORDS" | jq -r '.result[].id' | while read -r record_id; do
    curl -s -X DELETE "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records/$record_id" \
         -H "Authorization: Bearer $CF_TOKEN" \
         -H "Content-Type: application/json" > /dev/null
done

# 添加新记录
jq -r '.result[].ip' $FINAL_IPS | while read -r ip; do
    curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records" \
         -H "Authorization: Bearer $CF_TOKEN" \
         -H "Content-Type: application/json" \
         --data '{"type":"A","name":"'"$CF_DOMAIN"'","content":"'"$ip"'","ttl":60,"proxied":false}' > /dev/null
done

echo -e "${GREEN}==============================================${PLAIN}"
echo -e "${GREEN}   搞定！速度最快的 10 个 IP 已解析到域名。   ${PLAIN}"
echo -e "${GREEN}==============================================${PLAIN}"
