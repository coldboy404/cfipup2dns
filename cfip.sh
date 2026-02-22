#!/bin/bash
set -euo pipefail

# ================= 配置区域 =================
PROJECT_DIR="${PROJECT_DIR:-/opt/montecarlo-ip-searcher}"
CONFIG_FILE="${CONFIG_FILE:-$PROJECT_DIR/config.json}"

# 优选策略（可通过环境变量覆盖）
IP_MODE="${IP_MODE:-both}"               # 4 | 6 | both（默认 both）
CIDR_FILE_OVERRIDE="${CIDR_FILE:-}"      # 显式指定 cidr 文件时优先（仅单模式时建议使用）
BUDGET="${BUDGET:-}"
TIMEOUT="${TIMEOUT:-}"
HEADS="${HEADS:-}"
ROUNDS="${ROUNDS:-}"
SKIP_FIRST="${SKIP_FIRST:-}"
COLO_ALLOW="${COLO_ALLOW:-}"
COLO_EXCLUDE="${COLO_EXCLUDE:-}"

DOWNLOAD_TOP="${DOWNLOAD_TOP:-50}"
DOWNLOAD_BYTES="${DOWNLOAD_BYTES:-5000000}"
DOWNLOAD_TIMEOUT="${DOWNLOAD_TIMEOUT:-8s}"
DOWNLOAD_URL="${DOWNLOAD_URL:-}"

CONCURRENCY="${CONCURRENCY:-50}"
TOP_TEST="${TOP_TEST:-50}"
TOP_N="${TOP_N:-5}"                      # 每个模式写入的 IP 数量（默认每种 5 个）
MCIS_EXTRA_ARGS="${MCIS_EXTRA_ARGS:-}"

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
  IP_MODE=4|6|both         选择 IPv4 / IPv6 / 双栈（默认 both）
  TOP_N=5                  每个模式写入 DNS 的 IP 数量（默认每种 5 个）
  TOP_TEST=50              搜索输出候选数量（用于二次筛选）
  DOWNLOAD_TOP=50          对前 N 名进行下载测速（0=关闭下载测速）
  DOWNLOAD_URL=https://... 自定义测速文件地址
  DOWNLOAD_BYTES=5000000   下载字节数（指定 DOWNLOAD_URL 时可设为 0）
  ROUNDS=6                 多次测试取平均
  SKIP_FIRST=1             跳过前 N 次
  COLO_ALLOW=HKG,SJC       仅保留指定机房
  COLO_EXCLUDE=LAX,DFW     排除指定机房
  BUDGET=3000              mcis 探测预算
  CONCURRENCY=100          并发数

示例：
  # 默认：同时更新 A(5) + AAAA(5)
  cfip

  # 仅 IPv4
  IP_MODE=4 TOP_N=3 cfip

  # 仅 IPv6
  IP_MODE=6 TOP_N=3 cfip
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

run_one_mode() {
  local mode="$1"
  local dns_type cidr_default cidr_file result_file final_ips search_top

  case "$mode" in
    4)
      dns_type="A"
      cidr_default="$PROJECT_DIR/ipv4cidr.txt"
      ;;
    6)
      dns_type="AAAA"
      cidr_default="$PROJECT_DIR/ipv6cidr.txt"
      ;;
    *)
      echo -e "${RED}[!] 不支持的模式: $mode${PLAIN}"
      exit 1
      ;;
  esac

  if [ -n "$CIDR_FILE_OVERRIDE" ]; then
    cidr_file="$CIDR_FILE_OVERRIDE"
  else
    cidr_file="$cidr_default"
  fi

  if [ ! -f "$cidr_file" ]; then
    echo -e "${YELLOW}[!] 跳过 IPv${mode}：CIDR 文件不存在: $cidr_file${PLAIN}"
    return 0
  fi

  result_file="$PROJECT_DIR/scan_results_v${mode}.log"
  final_ips="$PROJECT_DIR/best_ips_v${mode}.json"

  mkdir -p "$PROJECT_DIR"
  cd "$PROJECT_DIR"
  rm -f "$result_file"

  echo -e "${GREEN}==============================================${PLAIN}"
  echo -e "${GREEN}   IPv${mode} 优选开始：目标最快速度 (Top ${TOP_N})   ${PLAIN}"
  echo -e "${GREEN}==============================================${PLAIN}"
  echo -e "${YELLOW}[*] 模式: IPv${mode} | CIDR: $(basename "$cidr_file")${PLAIN}"
  echo -e "${YELLOW}[*] 参数: budget=${BUDGET:-默认} top_test=${TOP_TEST} download_top=${DOWNLOAD_TOP} rounds=${ROUNDS:-默认} timeout=${TIMEOUT:-默认}${PLAIN}"

  search_top="$TOP_TEST"
  if [ "$TOP_N" -gt "$search_top" ]; then search_top="$TOP_N"; fi

  MCIS_ARGS=(
    -cidr-file "$cidr_file"
    -concurrency "$CONCURRENCY"
    -top "$search_top"
    -out jsonl
    -v
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
    [ -n "$DOWNLOAD_URL" ] && MCIS_ARGS+=( -download-url "$DOWNLOAD_URL" )
    [ -n "$DOWNLOAD_BYTES" ] && MCIS_ARGS+=( -download-bytes "$DOWNLOAD_BYTES" )
  fi

  # shellcheck disable=SC2206
  EXTRA_ARGS=( $MCIS_EXTRA_ARGS )

  set +e
  "$PROJECT_DIR/montecarlo-ip-searcher" "${MCIS_ARGS[@]}" "${EXTRA_ARGS[@]}" > "$result_file" 2>&1
  mcis_code=$?
  set -e

  if [ "$mcis_code" -ne 0 ]; then
    echo -e "${RED}[!] mcis 执行失败（退出码: ${mcis_code}）${PLAIN}"
    tail -n 40 "$result_file" 2>/dev/null || true
    return 1
  fi

  if [ ! -s "$result_file" ]; then
    echo -e "${RED}[!] IPv${mode} 扫描结果为空${PLAIN}"
    return 1
  fi

  JSON_LINES=$(grep -E '^\s*\{' "$result_file" || true)
  if [ -z "$JSON_LINES" ]; then
    echo -e "${RED}[!] IPv${mode} 结果中未找到可解析 JSON 行${PLAIN}"
    tail -n 20 "$result_file" || true
    return 1
  fi

  printf '%s\n' "$JSON_LINES" | jq -s --argjson top "$TOP_N" '
    map(select((.ip // "") != ""))
    | unique_by(.ip)
    | (map(select((.download_mbps // 0) > 0)) | sort_by(.download_mbps) | reverse) as $dl
    | if ($dl | length) > 0 then
        { mode: "download_mbps", result: ($dl[0:$top]) }
      else
        { mode: "score_ms", result: (map(select((.score_ms // 0) > 0)) | sort_by(.score_ms) | .[0:$top]) }
      end
  ' > "$final_ips"

  IP_COUNT=$(jq '.result | length' "$final_ips")
  SORT_MODE=$(jq -r '.mode' "$final_ips")
  if [ "$IP_COUNT" -eq 0 ]; then
    echo -e "${RED}[!] IPv${mode} 未找到有效 IP${PLAIN}"
    return 1
  fi

  echo -e "${GREEN}[+] IPv${mode} 筛选成功！模式: ${SORT_MODE}，共 $IP_COUNT 个 IP${PLAIN}"

  EXISTING_RECORDS=$(cf_api GET "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records?type=$dns_type&name=$CF_DOMAIN")
  cf_api_check "$EXISTING_RECORDS"

  jq -r '.result[].id' <<< "$EXISTING_RECORDS" | while read -r record_id; do
    if [ -n "$record_id" ] && [ "$record_id" != "null" ]; then
      DEL_RESP=$(cf_api DELETE "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records/$record_id")
      cf_api_check "$DEL_RESP"
    fi
  done

  jq -r '.result[].ip' "$final_ips" | while read -r ip; do
    [ -z "$ip" ] && continue
    ADD_RESP=$(cf_api POST "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records" \
      --data '{"type":"'"$dns_type"'","name":"'"$CF_DOMAIN"'","content":"'"$ip"'","ttl":'"$CF_TTL"',"proxied":'"$CF_PROXIED"'}')
    cf_api_check "$ADD_RESP"
  done

  echo -e "${GREEN}[✓] IPv${mode} DNS 更新完成（${dns_type}，${IP_COUNT} 条）${PLAIN}"
}

case "$IP_MODE" in
  4)
    run_one_mode 4
    ;;
  6)
    run_one_mode 6
    ;;
  both)
    run_one_mode 4
    run_one_mode 6
    ;;
  *)
    echo -e "${RED}[!] IP_MODE 仅支持 4 / 6 / both，当前: $IP_MODE${PLAIN}"
    exit 1
    ;;
esac

echo -e "${GREEN}==============================================${PLAIN}"
echo -e "${GREEN}   搞定！当前模式 ${IP_MODE}，每种写入 TOP_N=${TOP_N}。   ${PLAIN}"
echo -e "${GREEN}==============================================${PLAIN}"
