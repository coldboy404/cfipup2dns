#!/bin/sh
set -eu

PROJECT_DIR="${PROJECT_DIR:-/data/project}"
GH_PROXY="${GH_PROXY:-https://gh-proxy.org/}"
MCIS_REF="${MCIS_REF:-main}"
MCIS_BIN="$PROJECT_DIR/montecarlo-ip-searcher"
MCIS_VERSION_FILE="$PROJECT_DIR/.mcis_version"
WANTED_VERSION="${MCIS_REF}+source"

log() {
  printf '%s\n' "$1"
}

fetch() {
  url="$1"
  dest="$2"
  python3 - "$url" "$dest" <<'PY'
import shutil, socket, sys, time, urllib.error, urllib.request
url, dest = sys.argv[1], sys.argv[2]
req = urllib.request.Request(url, headers={"User-Agent": "cfipup2dns/2.0"})
last_err = None
for attempt in range(1, 5):
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
        sys.exit(0)
    except Exception as e:
        last_err = e
        retryable = isinstance(e, (urllib.error.URLError, ConnectionResetError, TimeoutError, socket.timeout, OSError, EOFError))
        if isinstance(e, urllib.error.HTTPError):
            retryable = e.code in (408, 409, 425, 429, 500, 502, 503, 504)
        if not retryable or attempt >= 4:
            raise
        delay = 1.5 * attempt
        print(f"[!] 下载失败，第 {attempt}/4 次重试前等待 {delay:.1f}s: {e}", flush=True)
        time.sleep(delay)
raise last_err
PY
}

fetch_with_fallback() {
  raw_url="$1"
  dest="$2"
  if [ -n "$GH_PROXY" ]; then
    if fetch "${GH_PROXY}${raw_url}" "$dest"; then
      return 0
    fi
    log "[!] 代理下载失败，回退直连"
  fi
  fetch "$raw_url" "$dest"
}

need_build=1
if [ -x "$MCIS_BIN" ] && [ -f "$MCIS_VERSION_FILE" ]; then
  current="$(cat "$MCIS_VERSION_FILE" 2>/dev/null || true)"
  if [ "$current" = "$WANTED_VERSION" ]; then
    need_build=0
  fi
fi

mkdir -p "$PROJECT_DIR"

if [ "$need_build" = "1" ]; then
  log "[*] 初始化 mcis：同步上游源码并预编译 -> $MCIS_REF"
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT INT TERM
  tarball="$tmpdir/src.tar.gz"

  if [ "$MCIS_REF" = "main" ]; then
    raw_tar="https://github.com/Leo-Mu/montecarlo-ip-searcher/archive/refs/heads/main.tar.gz"
    extracted="$tmpdir/montecarlo-ip-searcher-main"
  else
    raw_tar="https://github.com/Leo-Mu/montecarlo-ip-searcher/archive/refs/tags/${MCIS_REF}.tar.gz"
    extracted="$tmpdir/montecarlo-ip-searcher-${MCIS_REF#v}"
  fi

  fetch_with_fallback "$raw_tar" "$tarball"
  tar -xzf "$tarball" -C "$tmpdir"

  export CGO_ENABLED=0
  export GOTOOLCHAIN=local
  export GOOS=linux
  case "$(uname -m)" in
    x86_64|amd64) export GOARCH=amd64 ;;
    aarch64|arm64) export GOARCH=arm64 ;;
    *) log "[错误] 不支持的架构: $(uname -m)"; exit 1 ;;
  esac

  (cd "$extracted" && go build -trimpath -ldflags '-s -w' -o "$MCIS_BIN" ./cmd/mcis) || {
    log "[错误] 初始化编译 mcis 失败"
    exit 1
  }
  chmod +x "$MCIS_BIN"
  printf '%s' "$WANTED_VERSION" > "$MCIS_VERSION_FILE"
  log "[✓] mcis 预编译完成: $WANTED_VERSION"
fi

for name in ipv4cidr.txt ipv6cidr.txt; do
  path="$PROJECT_DIR/$name"
  if [ ! -s "$path" ]; then
    fetch_with_fallback "https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/$name" "$path"
  fi
done

"$MCIS_BIN" -h 2>&1 | grep -q -- '-download-mode' || {
  log "[错误] 当前 mcis 不支持 -download-mode"
  exit 1
}
