#!/bin/bash
set -euo pipefail

# ================= 配置区域 =================
PROJECT_DIR="${PROJECT_DIR:-/opt/montecarlo-ip-searcher}"
CONFIG_FILE="${CONFIG_FILE:-$PROJECT_DIR/config.json}"
RESULT_FILE="$PROJECT_DIR/scan_results.log"
FINAL_IPS="$PROJECT_DIR/best_ips.json"

# 优选策略（可通过环境变量覆盖）
IP_MODE="${IP_MODE:-4}"                  # 4 或 6
CIDR_FILE_OVERRIDE="${CIDR_FILE:-}"      # 显式指定 cidr 文件时优先
BUDGET="${BUDGET:-}"                     # mcis --budget（留空则用上游默认）
TIMEOUT="${TIMEOUT:-}"                   # mcis --timeout（留空则用上游默认）
HEADS="${HEADS:-}"                       # mcis --heads
ROUNDS="${ROUNDS:-}"                     # mcis --rounds（原作新增）
SKIP_FIRST="${SKIP_FIRST:-}"             # mcis --skip-first（原作新增）
COLO_ALLOW="${COLO_ALLOW:-}"             # mcis --colo，如 HKG,SJC
COLO_EXCLUDE="${COLO_EXCLUDE:-}"         # mcis --colo-exclude，如 LAX,DFW

DOWNLOAD_TOP="${DOWNLOAD_TOP:-50}"       # 对 Top N 做下载测速
DOWNLOAD_BYTES="${DOWNLOAD_BYTES:-5000000}"
DOWNLOAD_TIMEOUT="${DOWNLOAD_TIMEOUT:-8s}"
DOWNLOAD_URL="${DOWNLOAD_URL:-}"         # 原作新增：自定义测速文件 URL

CONCURRENCY="${CONCURRENCY:-50}"
TOP_TEST="${TOP_TEST:-50}"               # 搜索输出候选数量（用于二次筛选）
TOP_N="${TOP_N:-5}"                      # 最终写入 DNS 的 IP 数量
MCIS_EXTRA_ARGS="${MCIS_EXTRA_ARGS:-}"   # 透传给 mcis 的额外参数

# DNS 行为（可通过 config.json 或环境变量控制）
CF_TTL_ENV="${CF_TTL:-}"
CF_PROXIED_ENV="${CF_PROXIED:-}"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
PLAIN='\033[0m'

usage() {
  cat <<EOF
用法: cfip [环境变量覆盖]

常用环境变量：
  IP_MODE=4|6              选择 IPv4 或 IPv6（默认 4）
  TOP_N=5                  最终写入 DNS 的 IP 数量
  TOP_TEST=50              搜索输出候选数量（用于二次筛选）
  DOWNLOAD_TOP=50          对前 N 名进行下载测速（0=关闭下载测速）
  DOWNLOAD_URL=https://... 自定义测速文件地址（原作 v0.2.3 新增能力）
  DOWNLOAD_BYTES=5000000   下载字节数（指定 DOWNLOAD_URL 时可设为 0 表示不限制）
  ROUNDS=6                 多次测试取平均（原作 v0.2.1 新增）
  SKIP_FIRST=1             跳过前 N 次（原作 v0.2.1 新增）
  COLO_ALLOW=HKG,SJC       仅保留指定机房（原作新增）
  COLO_EXCLUDE=LAX,DFW     排除指定机房（原作新增）
  BUDGET=3000              mcis 探测预算
  CONCURRENCY=100          并发数

示例：
  IP_MODE=6 TOP_N=3 TOP_TEST=30 DOWNLOAD_TOP=20 cfip
  COLO_ALLOW=HKG,SJC ROUNDS=6 SKIP_FIRST=1 cfip
EOF
}

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo -e "${RED}[!] 缺少依赖: $1${PLAIN}"; exit 1; }; }
need_cmd jq
need_cmd curl

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [ ! -f "$CONFIG_FILE" ]; then
  echo -e "${RED}[!] 找不到配置文件: $CONFIG_FILE${PLAIN}"
  exit 1
fi

CF_TOKEN=$(jq -r '.cloudflare.token' "$CONFIG_FILE")
CF_ZONE_ID=$(jq -r '.cloudflare.zone_id' "$CONFIG_FILE")
CF_DOMAIN=$(jq -r '.cloudflare.domain' "$CONFIG_FILE")
CF_TTL=$(jq -r '.cloudflare.ttl // 60' "$CONFIG_FILE")
CF_PROXIED=$(jq -r '.cloudflare.proxied // false' "$CONFIG_FILE")

# 环境变量优先覆盖 config.json
[ -n "$CF_TTL_ENV" ] && CF_TTL="$CF_TTL_ENV"
[ -n "$CF_PROXIED_ENV" ] && CF_PROXIED="$CF_PROXIED_ENV"

if [ -z "$CF_TOKEN" ] || [ "$CF_TOKEN" == "null" ]; then
  echo -e "${RED}[!] 错误：配置文件中未找到 Cloudflare Token${PLAIN}"
  exit 1
fi

if [ ! -x "$PROJECT_DIR/montecarlo-ip-searcher" ]; then
  echo -e "${RED}[!] 未找到 montecarlo-ip-searcher 可执行文件${PLAIN}"
  exit 1
fi

case "$IP_MODE" in
  4) DEFAULT_CIDR_FILE="$PROJECT_DIR/ipv4cidr.txt"; DNS_TYPE="A" ;;
  6) DEFAULT_CIDR_FILE="$PROJECT_DIR/ipv6cidr.txt"; DNS_TYPE="AAAA" ;;
  *)
    echo -e "${RED}[!] IP_MODE 仅支持 4 或 6，当前: $IP_MODE${PLAIN}"
    exit 1
    ;;
esac
CIDR_FILE="${CIDR_FILE_OVERRIDE:-$DEFAULT_CIDR_FILE}"

if [ ! -f "$CIDR_FILE" ]; then
  echo -e "${RED}[!] 找不到 CIDR 文件: $CIDR_FILE${PLAIN}"
  exit 1
fi

mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"
rm -f "$RESULT_FILE"

# 1. 运行扫描
echo -e "${GREEN}==============================================${PLAIN}"
echo -e "${GREEN}   开始优选：目标最快速度 (Top ${TOP_N})   ${PLAIN}"
echo -e "${GREEN}==============================================${PLAIN}"

echo -e "${YELLOW}[*] 模式: IPv${IP_MODE} | CIDR: $(basename "$CIDR_FILE")${PLAIN}"
echo -e "${YELLOW}[*] 正在进行测速扫描...${PLAIN}"

SEARCH_TOP="$TOP_TEST"
if [ "$TOP_N" -gt "$SEARCH_TOP" ]; then SEARCH_TOP="$TOP_N"; fi

MCIS_ARGS=(
  -cidr-file "$CIDR_FILE"
  -concurrency "$CONCURRENCY"
  -top "$SEARCH_TOP"
  -out jsonl
)

[ -n "$BUDGET" ] && MCIS_ARGS+=( -budget "$BUDGET" )
[ -n "$TIMEOUT" ] && MCIS_ARGS+=( -timeout "$TIMEOUT" )
[ -n "$HEADS" ] && MCIS_ARGS+=( -heads "$HEADS" )
[ -n "$ROUNDS" ] && MCIS_ARGS+=( -rounds "$ROUNDS" )
[ -n "$SKIP_FIRST" ] && MCIS_ARGS+=( -skip-first "$SKIP_FIRST" )
[ -n "$COLO_ALLOW" ] && MCIS_ARGS+=( -colo "$COLO_ALLOW" )
[ -n "$COLO_EXCLUDE" ] && MCIS_ARGS+=( -colo-exclude "$COLO_EXCLUDE" )

if [ "$DOWNLOAD_TOP" -gt 0 ]; then
  MCIS_ARGS+=( -download-top "$DOWNLOAD_TOP" -download-timeout "$DOWNLOAD_TIMEOUT" )
  # 指定 DOWNLOAD_URL 时，允许 DOWNLOAD_BYTES=0（原作 v0.2.3 语义）
  [ -n "$DOWNLOAD_URL" ] && MCIS_ARGS+=( -download-url "$DOWNLOAD_URL" )
  if [ -n "$DOWNLOAD_BYTES" ]; then
    MCIS_ARGS+=( -download-bytes "$DOWNLOAD_BYTES" )
  fi
fi

# shellcheck disable=SC2206
EXTRA_ARGS=( $MCIS_EXTRA_ARGS )

"$PROJECT_DIR/montecarlo-ip-searcher" "${MCIS_ARGS[@]}" "${EXTRA_ARGS[@]}" > "$RESULT_FILE" 2>&1

if [ ! -s "$RESULT_FILE" ]; then
  echo -e "${RED}[!] 扫描结果为空${PLAIN}"
  exit 1
fi

# 2. 数据处理：优先按 download_mbps 排序；若未开启下载测速则按 score_ms 升序
# 只解析 JSON 行，忽略潜在非 JSON 输出
JSON_LINES=$(grep -E '^\s*\{' "$RESULT_FILE" || true)
if [ -z "$JSON_LINES" ]; then
  echo -e "${RED}[!] 扫描结果中未找到可解析 JSON 行${PLAIN}"
  echo -e "${YELLOW}[*] 最近日志:${PLAIN}"
  tail -n 20 "$RESULT_FILE" || true
  exit 1
fi

echo -e "${YELLOW}[*] 正在筛选候选 IP（优先下载速度，其次延迟）...${PLAIN}"

printf '%s\n' "$JSON_LINES" | jq -s --argjson top "$TOP_N" '
  map(select((.ip // "") != ""))
  | unique_by(.ip)
  | (map(select((.download_mbps // 0) > 0)) | sort_by(.download_mbps) | reverse) as $dl
  | if ($dl | length) > 0 then
      { mode: "download_mbps", result: ($dl[0:$top]) }
    else
      { mode: "score_ms", result: (map(select((.score_ms // 0) > 0)) | sort_by(.score_ms) | .[0:$top]) }
    end
' > "$FINAL_IPS"

IP_COUNT=$(jq '.result | length' "$FINAL_IPS")
SORT_MODE=$(jq -r '.mode' "$FINAL_IPS")
if [ "$IP_COUNT" -eq 0 ]; then
  echo -e "${RED}[!] 未找到有效 IP！可能是扫描时间太短或网络波动。${PLAIN}"
  tail -n 20 "$RESULT_FILE" || true
  exit 1
fi

echo -e "${GREEN}[+] 筛选成功！模式: ${SORT_MODE}，共 $IP_COUNT 个 IP：${PLAIN}"
printf "%-40s %-16s %-12s %-10s\n" "IP地址" "下载速度" "延迟分数" "地区"
echo "--------------------------------------------------------------------------------"
jq -r '.result[] | [(.ip // "-"), ((.download_mbps|tostring) + " Mbps"), ((.score_ms|tostring) + " ms"), (.trace.colo // "-")] | @tsv' "$FINAL_IPS" | \
  awk -F'\t' '{printf "%-40s %-16s %-12s %-10s\n", $1, $2, $3, $4}'

# 3. 上传到 Cloudflare
cf_api() {
  local method="$1"; shift
  local url="$1"; shift
  curl -sS -X "$method" "$url" \
    -H "Authorization: Bearer $CF_TOKEN" \
    -H "Content-Type: application/json" \
    "$@"
}

cf_api_check() {
  local resp="$1"
  local ok
  ok=$(jq -r '.success // false' <<< "$resp" 2>/dev/null || echo false)
  if [ "$ok" != "true" ]; then
    echo -e "${RED}[!] Cloudflare API 调用失败${PLAIN}"
    echo "$resp" | jq . 2>/dev/null || echo "$resp"
    exit 1
  fi
}

echo -e "${YELLOW}[*] 正在更新 Cloudflare DNS 记录 (${DNS_TYPE})...${PLAIN}"

EXISTING_RECORDS=$(cf_api GET "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records?type=$DNS_TYPE&name=$CF_DOMAIN")
cf_api_check "$EXISTING_RECORDS"

# 删除旧记录
jq -r '.result[].id' <<< "$EXISTING_RECORDS" | while read -r record_id; do
  if [ -n "$record_id" ] && [ "$record_id" != "null" ]; then
    DEL_RESP=$(cf_api DELETE "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records/$record_id")
    cf_api_check "$DEL_RESP"
  fi
done

# 添加新记录
jq -r '.result[].ip' "$FINAL_IPS" | while read -r ip; do
  [ -z "$ip" ] && continue
  ADD_RESP=$(cf_api POST "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records" \
    --data '{"type":"'"$DNS_TYPE"'","name":"'"$CF_DOMAIN"'","content":"'"$ip"'","ttl":'"$CF_TTL"',"proxied":'"$CF_PROXIED"'}')
  cf_api_check "$ADD_RESP"
done

echo -e "${GREEN}==============================================${PLAIN}"
echo -e "${GREEN}   搞定！已将最快 ${TOP_N} 个 IPv${IP_MODE} IP 更新到 DNS。   ${PLAIN}"
echo -e "${GREEN}==============================================${PLAIN}"
