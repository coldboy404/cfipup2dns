#!/bin/bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
PLAIN='\033[0m'

if [[ ${EUID:-0} -ne 0 ]]; then
  echo -e "${RED}[!] 请使用 root 运行 install.sh${PLAIN}"
  exit 1
fi

PROJECT_DIR="/opt/montecarlo-ip-searcher"
TMP_ASSETS_DIR="/tmp/cfipup2dns-assets"
RAW_BASE="https://raw.githubusercontent.com/coldboy404/cfipup2dns/main"
RAW_BASE_PROXY="https://gh-proxy.com/https://raw.githubusercontent.com/coldboy404/cfipup2dns/main"
UPSTREAM_REPO="https://github.com/Leo-Mu/montecarlo-ip-searcher.git"
UPSTREAM_REPO_PROXY="https://gh-proxy.com/https://github.com/Leo-Mu/montecarlo-ip-searcher.git"
CONFIG_FILE="$PROJECT_DIR/config.json"
CONFIG_BAK="/tmp/cfipup2dns-config.backup.json"

mkdir -p "$TMP_ASSETS_DIR" "$PROJECT_DIR"

log() { echo -e "$1"; }

fetch_asset() {
  local name="$1"
  local dst="$TMP_ASSETS_DIR/$name"
  curl -fsSL "$RAW_BASE/$name" -o "$dst" \
    || curl -fsSL "$RAW_BASE_PROXY/$name" -o "$dst"
}

detect_arch() {
  case "$(uname -m)" in
    x86_64|amd64)
      MCIS_ARCH="amd64"
      GO_ARCH="amd64"
      ;;
    aarch64|arm64)
      MCIS_ARCH="arm64"
      GO_ARCH="arm64"
      ;;
    *)
      echo -e "${RED}[!] 不支持的架构: $(uname -m)（当前仅支持 amd64/arm64）${PLAIN}"
      return 1
      ;;
  esac
}

install_prereqs() {
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y jq wget git curl ca-certificates tar cron || \
    apt-get install -y jq wget git curl ca-certificates tar
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y jq wget git curl ca-certificates tar cronie || \
    dnf install -y jq wget git curl ca-certificates tar
  elif command -v yum >/dev/null 2>&1; then
    yum install -y jq wget git curl ca-certificates tar cronie || \
    yum install -y jq wget git curl ca-certificates tar
  elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive install jq wget git curl ca-certificates tar cron || \
    zypper --non-interactive install jq wget git curl ca-certificates tar
  elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm jq wget git curl ca-certificates tar cronie || \
    pacman -Sy --noconfirm jq wget git curl ca-certificates tar
  else
    echo -e "${RED}[!] 未识别的包管理器（支持 apt/dnf/yum/zypper/pacman）${PLAIN}"
    return 1
  fi
}

version_ge() {
  # version_ge 2.34 2.32 => true
  [ "$(printf '%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

should_try_prebuilt() {
  local mode glibc_ver
  mode="${MCIS_INSTALL_MODE:-auto}"  # auto|prebuilt|source
  case "$mode" in
    prebuilt) return 0 ;;
    source) return 1 ;;
  esac

  glibc_ver="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')"
  if [[ -n "$glibc_ver" ]] && ! version_ge "$glibc_ver" "2.32"; then
    echo -e "${YELLOW}[*] 检测到 glibc ${glibc_ver} < 2.32，跳过预编译包，直接源码编译${PLAIN}"
    return 1
  fi
  return 0
}

download_mcis_release() {
  local tag url url_proxy tgz
  tag="${MCIS_TAG:-v0.2.3}"
  tgz="$PROJECT_DIR/mcis-release.tgz"
  url="https://github.com/Leo-Mu/montecarlo-ip-searcher/releases/download/${tag}/mcis-${tag}-linux-${MCIS_ARCH}.tar.gz"
  url_proxy="https://gh-proxy.com/${url}"

  echo -e "${YELLOW}[*] 下载 mcis 预编译包: ${tag} (${MCIS_ARCH})${PLAIN}"
  # 防止网络卡死：超时后切换备用源，仍失败则回退源码编译
  curl -fL --connect-timeout 6 --max-time 45 --retry 1 "$url" -o "$tgz" \
    || curl -fL --connect-timeout 6 --max-time 45 --retry 1 "$url_proxy" -o "$tgz"

  tar -xzf "$tgz" -C "$PROJECT_DIR"
  rm -f "$tgz"

  if [[ ! -x "$PROJECT_DIR/mcis" ]]; then
    echo -e "${RED}[!] mcis 解压后不可执行${PLAIN}"
    return 1
  fi

  mv -f "$PROJECT_DIR/mcis" "$PROJECT_DIR/montecarlo-ip-searcher"
  chmod +x "$PROJECT_DIR/montecarlo-ip-searcher"
}

install_go() {
  local GO_VERSION="1.25.5"
  local GO_TARBALL="go${GO_VERSION}.linux-${GO_ARCH}.tar.gz"
  if [[ ! -x /usr/local/go/bin/go ]] || [[ "$(/usr/local/go/bin/go version 2>/dev/null | awk '{print $3}')" != "go${GO_VERSION}" ]]; then
    rm -rf /usr/local/go
    wget -q --show-progress "https://go.dev/dl/${GO_TARBALL}" -O /tmp/go.tar.gz \
      || curl -fL "https://go.dev/dl/${GO_TARBALL}" -o /tmp/go.tar.gz
    tar -C /usr/local -xzf /tmp/go.tar.gz
    rm -f /tmp/go.tar.gz
  fi
  export PATH="/usr/local/go/bin:$PATH"
  export GOTOOLCHAIN=local
  go env -w GOPROXY=https://goproxy.cn,direct || true
}

build_mcis_from_source() {
  echo -e "${YELLOW}[*] 使用源码编译 mcis（兼容旧 glibc 系统）...${PLAIN}"
  install_go

  if [[ -d "$PROJECT_DIR/.git" ]]; then
    git -C "$PROJECT_DIR" fetch --all --tags
    git -C "$PROJECT_DIR" reset --hard origin/main
  else
    rm -rf "$PROJECT_DIR"
    git clone "$UPSTREAM_REPO" "$PROJECT_DIR" || git clone "$UPSTREAM_REPO_PROXY" "$PROJECT_DIR"
  fi

  cd "$PROJECT_DIR"
  GOTOOLCHAIN=local go mod tidy
  if ! GOTOOLCHAIN=local go build -o montecarlo-ip-searcher ./cmd/mcis; then
    local MAIN_FILE
    MAIN_FILE=$(find . -name main.go -print0 | xargs -0 grep -l "package main" | head -n 1)
    [[ -z "$MAIN_FILE" ]] && { echo -e "${RED}[!] 构建失败：未找到 main.go${PLAIN}"; return 1; }
    GOTOOLCHAIN=local go build -o montecarlo-ip-searcher "$(dirname "$MAIN_FILE")"
  fi
  chmod +x "$PROJECT_DIR/montecarlo-ip-searcher"
}

binary_self_check() {
  set +e
  "$PROJECT_DIR/montecarlo-ip-searcher" --help >/tmp/mcis-selfcheck.log 2>&1
  local code=$?
  set -e
  if [[ $code -eq 0 ]]; then
    return 0
  fi
  if grep -q 'GLIBC_' /tmp/mcis-selfcheck.log; then
    return 2
  fi
  return 1
}

write_config() {
  local cfg="$1" token="$2" zone="$3" domain="$4" ttl="$5" proxied="$6"
  cat > "$cfg" <<EOF
{
  "cloudflare": {
    "token": "$token",
    "zone_id": "$zone",
    "domain": "$domain",
    "ttl": $ttl,
    "proxied": $proxied
  }
}
EOF
}

setup_cron() {
  if ! command -v crontab >/dev/null 2>&1; then
    echo -e "${YELLOW}[!] 未检测到 crontab，已跳过定时任务设置（可手动安装 cron/cronie）${PLAIN}"
    return 0
  fi
  local CRON_TMP
  CRON_TMP=$(mktemp)
  crontab -l 2>/dev/null | grep -v "cfip-run" > "$CRON_TMP" || true
  echo "0 */2 * * * /bin/bash /usr/local/bin/cfip-run >> $PROJECT_DIR/cron.log 2>&1" >> "$CRON_TMP"
  echo "@reboot sleep 60 && /bin/bash /usr/local/bin/cfip-run >> $PROJECT_DIR/boot.log 2>&1" >> "$CRON_TMP"
  crontab "$CRON_TMP"
  rm -f "$CRON_TMP"
}

# 更新时先备份旧配置，避免目录刷新导致丢失
if [[ -f "$CONFIG_FILE" ]]; then
  cp -f "$CONFIG_FILE" "$CONFIG_BAK" || true
fi

detect_arch

echo -e "${GREEN}[*] 1/6 安装基础依赖（跨发行版）...${PLAIN}"
install_prereqs

echo -e "${GREEN}[*] 2/6 准备运行脚本资源...${PLAIN}"
fetch_asset cfip.sh
fetch_asset menu.sh
chmod +x "$TMP_ASSETS_DIR/cfip.sh" "$TMP_ASSETS_DIR/menu.sh"

echo -e "${GREEN}[*] 3/6 安装 mcis...${PLAIN}"
if should_try_prebuilt; then
  if download_mcis_release; then
    if binary_self_check; then
      echo -e "${GREEN}[+] 预编译 mcis 可用${PLAIN}"
    else
      rc=$?
      if [[ $rc -eq 2 ]]; then
        build_mcis_from_source
      else
        echo -e "${YELLOW}[!] 预编译 mcis 自检失败，自动切源码编译${PLAIN}"
        build_mcis_from_source
      fi
    fi
  else
    echo -e "${YELLOW}[!] 预编译下载失败或超时，自动切源码编译（更稳）${PLAIN}"
    build_mcis_from_source
  fi
else
  build_mcis_from_source
fi

echo -e "${GREEN}[*] 4/6 确认 CIDR 文件...${PLAIN}"
[[ -s "$PROJECT_DIR/ipv4cidr.txt" ]] || {
  curl -fsSL https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv4cidr.txt -o "$PROJECT_DIR/ipv4cidr.txt" \
    || curl -fsSL https://gh-proxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv4cidr.txt -o "$PROJECT_DIR/ipv4cidr.txt"
}
[[ -s "$PROJECT_DIR/ipv6cidr.txt" ]] || {
  curl -fsSL https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv6cidr.txt -o "$PROJECT_DIR/ipv6cidr.txt" \
    || curl -fsSL https://gh-proxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/ipv6cidr.txt -o "$PROJECT_DIR/ipv6cidr.txt"
}

echo -e "${GREEN}[*] 5/6 处理配置...${PLAIN}"
# 优先使用当前配置，其次使用更新前备份配置
if [[ ! -f "$CONFIG_FILE" && -f "$CONFIG_BAK" ]]; then
  cp -f "$CONFIG_BAK" "$CONFIG_FILE" || true
fi

OLD_TOKEN=""; OLD_ZONE=""; OLD_DOMAIN=""; OLD_TTL="60"; OLD_PROXIED="false"
if [[ -f "$CONFIG_FILE" ]]; then
  OLD_TOKEN=$(jq -r '.cloudflare.token // ""' "$CONFIG_FILE" 2>/dev/null || true)
  OLD_ZONE=$(jq -r '.cloudflare.zone_id // ""' "$CONFIG_FILE" 2>/dev/null || true)
  OLD_DOMAIN=$(jq -r '.cloudflare.domain // ""' "$CONFIG_FILE" 2>/dev/null || true)
  OLD_TTL=$(jq -r '.cloudflare.ttl // 60' "$CONFIG_FILE" 2>/dev/null || echo 60)
  OLD_PROXIED=$(jq -r '.cloudflare.proxied // false' "$CONFIG_FILE" 2>/dev/null || echo false)
fi

CF_KEY="${CF_TOKEN:-$OLD_TOKEN}"
CF_ZONE="${CF_ZONE_ID:-$OLD_ZONE}"
CF_DOMAIN="${CF_DOMAIN:-$OLD_DOMAIN}"
CF_TTL="${CF_TTL:-$OLD_TTL}"
CF_PROXIED="${CF_PROXIED:-$OLD_PROXIED}"

if [[ -n "$CF_KEY" && -n "$CF_ZONE" && -n "$CF_DOMAIN" ]]; then
  write_config "$CONFIG_FILE" "$CF_KEY" "$CF_ZONE" "$CF_DOMAIN" "$CF_TTL" "$CF_PROXIED"
  rm -f "$CONFIG_BAK" 2>/dev/null || true
  echo -e "${GREEN}[+] 已保留/写入配置: $CONFIG_FILE${PLAIN}"
else
  echo -e "${YELLOW}[!] 未检测到完整 Cloudflare 配置，已跳过配置写入。${PLAIN}"
  echo -e "${YELLOW}    可在菜单中选择“2) 修改 Cloudflare 配置”后再运行。${PLAIN}"
fi

echo -e "${GREEN}[*] 6/6 安装命令与定时任务...${PLAIN}"
install -m 755 "$TMP_ASSETS_DIR/cfip.sh" /usr/local/bin/cfip-run
install -m 755 "$TMP_ASSETS_DIR/menu.sh" /usr/local/bin/cfip
install -m 755 "$TMP_ASSETS_DIR/menu.sh" /usr/local/bin/cfip-menu
setup_cron

echo -e "${GREEN}=============================================${PLAIN}"
echo -e "${GREEN}安装完成！${PLAIN}"
echo -e "${GREEN}- 直接优选: cfip-run${PLAIN}"
echo -e "${GREEN}- 菜单管理: cfip${PLAIN}"
echo -e "${GREEN}=============================================${PLAIN}"
