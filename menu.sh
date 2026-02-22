#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="/opt/montecarlo-ip-searcher"
CONFIG_FILE="$PROJECT_DIR/config.json"

# 当通过 curl | bash 运行时，$0 不是仓库文件路径，需临时拉取仓库脚本
ensure_repo_scripts() {
  if [[ -f "$REPO_DIR/install.sh" && -f "$REPO_DIR/uninstall.sh" ]]; then
    return 0
  fi

  local tmp_dir="/tmp/cfipup2dns-menu"
  rm -rf "$tmp_dir"
  mkdir -p "$tmp_dir"

  echo -e "${YELLOW}[*] 检测到非仓库环境，正在临时获取脚本...${PLAIN}"
  if command -v git >/dev/null 2>&1; then
    git clone --depth=1 https://github.com/coldboy404/cfipup2dns.git "$tmp_dir" \
      || git clone --depth=1 https://gh-proxy.com/https://github.com/coldboy404/cfipup2dns.git "$tmp_dir"
  else
    curl -fsSL https://raw.githubusercontent.com/coldboy404/cfipup2dns/main/install.sh -o "$tmp_dir/install.sh"
    curl -fsSL https://raw.githubusercontent.com/coldboy404/cfipup2dns/main/uninstall.sh -o "$tmp_dir/uninstall.sh"
    curl -fsSL https://raw.githubusercontent.com/coldboy404/cfipup2dns/main/cfip.sh -o "$tmp_dir/cfip.sh"
    curl -fsSL https://raw.githubusercontent.com/coldboy404/cfipup2dns/main/menu.sh -o "$tmp_dir/menu.sh"
  fi

  REPO_DIR="$tmp_dir"
}

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

pause() {
  read -rp "按回车继续..." _
}

show_header() {
  clear || true
  echo -e "${CYAN}===============================================${PLAIN}"
  echo -e "${CYAN}        cfipup2dns 一键部署管理菜单${PLAIN}"
  echo -e "${CYAN}===============================================${PLAIN}"
  echo "仓库目录: $REPO_DIR"
  echo "项目目录: $PROJECT_DIR"
  echo
}

quick_deploy() {
  echo -e "${YELLOW}[*] 执行快速部署：安装/更新 -> 配置 -> 首次运行${PLAIN}"
  bash "$REPO_DIR/install.sh"
  echo
  read -rp "是否立即执行一次优选？(Y/n): " ans
  ans=${ans:-Y}
  if [[ "$ans" =~ ^[Yy]$ ]]; then
    /usr/local/bin/cfip-run || true
  fi
}

install_or_update() {
  bash "$REPO_DIR/install.sh"
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
  read -rp "API Token [已隐藏，留空保持不变]: " new_token
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
  echo -e "${YELLOW}[*] 运行一次优选${PLAIN}"
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
    echo -e "${YELLOW}[!] 还没有 cron.log${PLAIN}"
  fi
}

check_status() {
  echo -e "${YELLOW}[*] 快速状态${PLAIN}"
  command -v /usr/local/bin/cfip-run >/dev/null 2>&1 && echo "cfip-run: 已安装" || echo "cfip-run: 未安装"
  command -v /usr/local/bin/cfip >/dev/null 2>&1 && echo "cfip(menu): 已安装" || echo "cfip(menu): 未安装"
  [[ -x "$PROJECT_DIR/montecarlo-ip-searcher" ]] && echo "mcis: 已安装" || echo "mcis: 未安装"
  [[ -f "$CONFIG_FILE" ]] && echo "config: $CONFIG_FILE" || echo "config: 未配置"
  echo
  crontab -l 2>/dev/null | grep cfip || echo "crontab: 未发现 cfip 定时任务"
}

need_root
ensure_repo_scripts

while true; do
  show_header
  echo "1) 快速部署（安装/更新 + 配置 + 首次运行）"
  echo "2) 安装 / 更新"
  echo "3) 修改 Cloudflare 配置"
  echo "4) 立即运行一次优选"
  echo "5) 查看日志（cron.log）"
  echo "6) 查看状态（安装/配置/定时任务）"
  echo "7) 卸载"
  echo "0) 退出"
  echo
  read -rp "请选择: " choice
  echo
  case "$choice" in
    1) quick_deploy; pause ;;
    2) install_or_update; pause ;;
    3) edit_config; pause ;;
    4) run_once; pause ;;
    5) show_logs; pause ;;
    6) check_status; pause ;;
    7) bash "$REPO_DIR/uninstall.sh"; pause ;;
    0) echo "已退出。"; exit 0 ;;
    *) echo -e "${RED}[!] 无效选项${PLAIN}"; pause ;;
  esac
done
