#!/bin/bash
set -euo pipefail

PROJECT_DIR="/opt/montecarlo-ip-searcher"
CONFIG_FILE="$PROJECT_DIR/config.json"

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
PLAIN='\033[0m'

need_root() {
  if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}[!] 请使用 root 运行${PLAIN}"
    exit 1
  fi
}

pause() { read -rp "按回车继续..." _; }

show_header() {
  clear || true
  echo -e "${CYAN}===============================================${PLAIN}"
  echo -e "${CYAN}        cfipup2dns 一键部署管理菜单${PLAIN}"
  echo -e "${CYAN}===============================================${PLAIN}"
  echo
}

run_install() {
  local tmp="/tmp/cfipup2dns-install.sh"
  echo -e "${YELLOW}[*] 正在获取最新版安装脚本...${PLAIN}"
  curl -fsSL https://raw.githubusercontent.com/coldboy404/cfipup2dns/main/install.sh -o "$tmp" \
    || curl -fsSL https://gh-proxy.com/https://raw.githubusercontent.com/coldboy404/cfipup2dns/main/install.sh -o "$tmp"
  chmod +x "$tmp"
  bash "$tmp"

  echo
  if [[ -f "$CONFIG_FILE" ]]; then
    echo -e "${GREEN}[+] 检测到配置文件已存在：$CONFIG_FILE${PLAIN}"
  else
    echo -e "${YELLOW}[!] 当前还没有配置文件。${PLAIN}"
    read -rp "现在开始填写 Cloudflare 配置？(Y/n): " ans
    ans=${ans:-Y}
    if [[ "$ans" =~ ^[Yy]$ ]]; then
      edit_config || true
    else
      echo -e "${YELLOW}[*] 已跳过。你可以稍后在菜单里选“2) 修改 Cloudflare 配置”。${PLAIN}"
    fi
  fi
}

edit_config() {
  mkdir -p "$PROJECT_DIR"

  local token="" zone_id="" domain="" ttl="60" proxied="false"
  if [[ -f "$CONFIG_FILE" ]]; then
    token=$(jq -r '.cloudflare.token // ""' "$CONFIG_FILE" 2>/dev/null || echo "")
    zone_id=$(jq -r '.cloudflare.zone_id // ""' "$CONFIG_FILE" 2>/dev/null || echo "")
    domain=$(jq -r '.cloudflare.domain // ""' "$CONFIG_FILE" 2>/dev/null || echo "")
    ttl=$(jq -r '.cloudflare.ttl // 60' "$CONFIG_FILE" 2>/dev/null || echo 60)
    proxied=$(jq -r '.cloudflare.proxied // false' "$CONFIG_FILE" 2>/dev/null || echo false)
  fi

  echo -e "${YELLOW}[*] 修改 Cloudflare 配置${PLAIN}"
  read -rp "API Token [留空保持不变]: " new_token
  read -rp "Zone ID [$zone_id]: " new_zone
  read -rp "域名 (如 cf.example.com) [$domain]: " new_domain
  read -rp "TTL [$ttl]: " new_ttl
  read -rp "proxied (true/false) [$proxied]: " new_proxied

  [[ -n "$new_token" ]] && token="$new_token"
  [[ -n "$new_zone" ]] && zone_id="$new_zone"
  [[ -n "$new_domain" ]] && domain="$new_domain"
  [[ -n "$new_ttl" ]] && ttl="$new_ttl"
  [[ -n "$new_proxied" ]] && proxied="$new_proxied"

  if [[ -z "$token" || -z "$zone_id" || -z "$domain" ]]; then
    echo -e "${RED}[!] token / zone_id / domain 不能为空${PLAIN}"
    return 1
  fi

  cat > "$CONFIG_FILE" <<EOF
{
  "cloudflare": {
    "token": "$token",
    "zone_id": "$zone_id",
    "domain": "$domain",
    "ttl": $ttl,
    "proxied": $proxied
  }
}
EOF
  echo -e "${GREEN}[+] 配置已保存：$CONFIG_FILE${PLAIN}"
}

run_once() {
  local mode topn
  if [[ ! -f "$CONFIG_FILE" ]]; then
    echo -e "${YELLOW}[!] 未找到配置文件：$CONFIG_FILE${PLAIN}"
    read -rp "是否先进入配置填写？(Y/n): " ans
    ans=${ans:-Y}
    if [[ "$ans" =~ ^[Yy]$ ]]; then
      edit_config || return 1
    else
      return 0
    fi
  fi

  echo -e "${YELLOW}[*] 运行一次优选（使用原作者默认参数）${PLAIN}"
  read -rp "模式 (4/6/both) [both]: " mode
  read -rp "每种写入数量 TOP_N [5]: " topn
  mode=${mode:-both}
  topn=${topn:-5}
  IP_MODE="$mode" TOP_N="$topn" /usr/local/bin/cfip-run
}

show_logs() {
  if [[ -f "$PROJECT_DIR/cron.log" ]]; then
    tail -n 80 "$PROJECT_DIR/cron.log"
  else
    echo -e "${YELLOW}[!] 还没有日志${PLAIN}"
  fi
}

check_status() {
  echo -e "${YELLOW}[*] 快速状态${PLAIN}"
  command -v /usr/local/bin/cfip-run >/dev/null 2>&1 && echo "cfip-run: 已安装" || echo "cfip-run: 未安装"
  command -v /usr/local/bin/cfip >/dev/null 2>&1 && echo "cfip: 已安装" || echo "cfip: 未安装"
  [[ -x "$PROJECT_DIR/montecarlo-ip-searcher" ]] && echo "mcis: 已安装" || echo "mcis: 未安装"
  [[ -f "$CONFIG_FILE" ]] && echo "config: 已配置" || echo "config: 未配置"
  echo
  crontab -l 2>/dev/null | grep cfip-run || echo "crontab: 未发现 cfip-run 定时任务"
}

run_uninstall() {
  local tmp="/tmp/cfipup2dns-uninstall.sh"
  local ts
  ts="$(date +%s)"
  curl -fsSL "https://raw.githubusercontent.com/coldboy404/cfipup2dns/main/uninstall.sh?t=${ts}" -o "$tmp" \
    || curl -fsSL "https://gh-proxy.com/https://raw.githubusercontent.com/coldboy404/cfipup2dns/main/uninstall.sh?t=${ts}" -o "$tmp"
  chmod +x "$tmp"
  bash "$tmp"
}

need_root

while true; do
  show_header
  echo "1) 安装 / 更新"
  echo "2) 修改 Cloudflare 配置"
  echo "3) 立即运行一次优选"
  echo "4) 查看日志"
  echo "5) 查看状态"
  echo "6) 卸载"
  echo "0) 退出"
  echo
  read -rp "请选择: " choice
  echo
  case "$choice" in
    1) run_install; pause ;;
    2) edit_config; pause ;;
    3) run_once; pause ;;
    4) show_logs; pause ;;
    5) check_status; pause ;;
    6) run_uninstall; pause ;;
    0) echo "已退出。"; exit 0 ;;
    *) echo -e "${RED}[!] 无效选项${PLAIN}"; pause ;;
  esac
done
