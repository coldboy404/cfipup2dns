#!/bin/bash
PROJECT_DIR="/opt/montecarlo-ip-searcher"
RESULT_FILE="$PROJECT_DIR/scan_results.log"
FINAL_IPS="$PROJECT_DIR/best_5_speed.json"
CF_TOKEN=$(jq -r .cloudflare.token $PROJECT_DIR/config.json)
CF_ZONE_ID=$(jq -r .cloudflare.zone_id $PROJECT_DIR/config.json)
CF_DOMAIN=$(jq -r .cloudflare.domain $PROJECT_DIR/config.json)
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
PLAIN='\033[0m'

if [ -z "$CF_TOKEN" ] || [ "$CF_TOKEN" == "null" ]; then
    echo -e "${RED}[!] 错误：未配置 Cloudflare Token${PLAIN}"; exit 1
fi

echo -e "${GREEN}=== 正在优选：目标最快速度 (Top 5) ===${PLAIN}"
cd $PROJECT_DIR
rm -f $RESULT_FILE

# 扫描
./montecarlo-ip-searcher --cidr-file ipv4cidr.txt > $RESULT_FILE 2>&1

# 筛选 Top 5
grep "^{" $RESULT_FILE | jq -s '
  map(select(.ok == true and .download_mbps > 0)) | 
  sort_by(.download_mbps) | reverse | 
  unique_by(.ip) | 
  .[0:5] | 
  {result: .}
' > $FINAL_IPS

IP_COUNT=$(jq '.result | length' $FINAL_IPS)
if [ "$IP_COUNT" -eq "0" ]; then echo -e "${RED}[!] 未找到有效 IP${PLAIN}"; exit 1; fi

echo -e "${GREEN}[+] 筛选出 $IP_COUNT 个最佳 IP：${PLAIN}"
jq -r '.result[] | "\(.ip) \t \(.download_mbps) Mbps"' $FINAL_IPS

# 上传
echo -e "${YELLOW}[*] 更新 DNS 记录...${PLAIN}"
EXISTING_RECORDS=$(curl -s -X GET "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records?type=A&name=$CF_DOMAIN" \
     -H "Authorization: Bearer $CF_TOKEN" -H "Content-Type: application/json")

echo "$EXISTING_RECORDS" | jq -r '.result[].id' | while read -r record_id; do
    curl -s -X DELETE "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records/$record_id" \
         -H "Authorization: Bearer $CF_TOKEN" -H "Content-Type: application/json" > /dev/null
done

jq -r '.result[].ip' $FINAL_IPS | while read -r ip; do
    curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records" \
         -H "Authorization: Bearer $CF_TOKEN" -H "Content-Type: application/json" \
         --data '{"type":"A","name":"'"$CF_DOMAIN"'","content":"'"$ip"'","ttl":60,"proxied":false}' > /dev/null
done
echo -e "${GREEN}=== 成功更新 5 个 IP ===${PLAIN}"
