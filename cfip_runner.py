#!/usr/bin/env python3
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(os.getenv("PROJECT_DIR", "/data/project"))
CONFIG_FILE = Path(os.getenv("CONFIG_FILE", str(PROJECT_DIR / "config.json")))
GH_PROXY = os.getenv("GH_PROXY", "https://gh-proxy.org/")
MCIS_REF = os.getenv("MCIS_REF", "main")
IP_MODE = os.getenv("IP_MODE", "both")
TOP_N = int(os.getenv("TOP_N", "5"))
TOP_TEST = int(os.getenv("TOP_TEST", "50"))
CONCURRENCY = os.getenv("CONCURRENCY", "200")
TIMEOUT = os.getenv("TIMEOUT", "3s")
ROUNDS = os.getenv("ROUNDS", "4")
BUDGET = os.getenv("BUDGET", "3000")
MCIS_HOST = os.getenv("MCIS_HOST", "").strip()
MCIS_PATH = os.getenv("MCIS_PATH", "/cdn-cgi/trace").strip() or "/cdn-cgi/trace"
HEADS_V4 = os.getenv("HEADS_V4", "4")
HEADS_V6 = os.getenv("HEADS_V6", "16")
BUDGET_V6 = os.getenv("BUDGET_V6", "4000")
CONCURRENCY_V6 = os.getenv("CONCURRENCY_V6", "100")
DOWNLOAD_TOP = os.getenv("DOWNLOAD_TOP", "20")
DOWNLOAD_TIMEOUT = os.getenv("DOWNLOAD_TIMEOUT", "45s")
DOWNLOAD_MODE = os.getenv("DOWNLOAD_MODE", "sequential")
DOWNLOAD_URL = os.getenv("DOWNLOAD_URL", "")
DOWNLOAD_BYTES = os.getenv("DOWNLOAD_BYTES", "50000000")
MAX_SCAN_SECONDS = int(os.getenv("MAX_SCAN_SECONDS", "0"))
PROBE_FALLBACK_HOST = os.getenv("PROBE_FALLBACK_HOST", "example.com").strip() or "example.com"
HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "4"))
HTTP_RETRY_DELAY = float(os.getenv("HTTP_RETRY_DELAY", "1.5"))
KEEP_LAST_ON_FAIL = os.getenv("KEEP_LAST_ON_FAIL", "true").strip().lower() not in ("0", "false", "no", "off")
MAX_LATENCY_MS = float(os.getenv("MAX_LATENCY_MS", "0") or 0)
MIN_LATENCY_MS = float(os.getenv("MIN_LATENCY_MS", "0") or 0)
MAX_LOSS_RATE = float(os.getenv("MAX_LOSS_RATE", "1") or 1)
MIN_DOWNLOAD_MBPS = float(os.getenv("MIN_DOWNLOAD_MBPS", "0") or 0)
CF_COLO = os.getenv("CF_COLO", "").strip().upper()


def log(msg):
    print(msg, flush=True)


def _is_retryable_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 409, 425, 429, 500, 502, 503, 504)
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (ConnectionResetError, TimeoutError, socket.timeout, OSError, EOFError)):
            return True
        return "reset by peer" in str(reason).lower() or "temporarily unavailable" in str(reason).lower()
    return isinstance(exc, (ConnectionResetError, TimeoutError, socket.timeout, OSError, EOFError))


def _with_retries(op_name, func, retries=HTTP_RETRIES, base_delay=HTTP_RETRY_DELAY):
    last_err = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            return func()
        except Exception as e:
            last_err = e
            if attempt >= max(1, retries) or not _is_retryable_error(e):
                raise
            delay = base_delay * attempt
            log(f"[!] {op_name} 失败，第 {attempt}/{retries} 次重试前等待 {delay:.1f}s: {e}")
            time.sleep(delay)
    raise last_err


def fetch(url, dest, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "cfipup2dns/2.0"})

    def _do_fetch():
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)

    return _with_retries(f"下载 {url}", _do_fetch)


def fetch_with_fallback(raw_url, dest, timeout=120):
    urls = []
    if GH_PROXY:
        urls.append(f"{GH_PROXY}{raw_url}")
    urls.append(raw_url)
    last_err = None
    for url in urls:
        try:
            log(f"[*] 下载: {url}")
            fetch(url, dest, timeout=timeout)
            return
        except Exception as e:
            last_err = e
            log(f"[!] 下载失败: {e}")
    raise last_err


def mcis_supports_flag(mcis_bin, flag_name):
    try:
        res = subprocess.run([str(mcis_bin), "-h"], capture_output=True, text=True, timeout=15)
        text = (res.stdout or "") + "\n" + (res.stderr or "")
        return flag_name in text
    except Exception:
        return False


def ensure_mcis():
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    mcis_bin = PROJECT_DIR / "montecarlo-ip-searcher"
    version_file = PROJECT_DIR / ".mcis_version"
    if not mcis_bin.exists():
        raise RuntimeError("mcis 未初始化，请先重建/重启容器完成预编译")
    current_version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else ""
    if current_version != f"{MCIS_REF}+source":
        log(f"[!] 当前 mcis 版本标记为: {current_version or 'unknown'}，目标: {MCIS_REF}+source")
    if not mcis_supports_flag(mcis_bin, "-download-mode"):
        raise RuntimeError("当前 mcis 不支持 -download-mode，请先在容器初始化阶段完成正确编译")
    for name in ("ipv4cidr.txt", "ipv6cidr.txt"):
        p = PROJECT_DIR / name
        if not p.exists() or p.stat().st_size == 0:
            raw = f"https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/{name}"
            fetch_with_fallback(raw, p, timeout=60)
    return mcis_bin


def load_config():
    if not CONFIG_FILE.exists():
        raise RuntimeError(f"配置文件不存在: {CONFIG_FILE}")
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def cf_request(method, path, token, payload=None):
    url = f"https://api.cloudflare.com/client/v4{path}"
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "cfipup2dns/2.0",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    def _do_request():
        with urllib.request.urlopen(req, timeout=60) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
        if not obj.get("success"):
            raise RuntimeError(json.dumps(obj, ensure_ascii=False))
        return obj

    return _with_retries(f"Cloudflare API {method} {path}", _do_request)


def normalize_records(cfg):
    cf = cfg.get("cloudflare", {}) if isinstance(cfg, dict) else {}
    records = []
    raw_records = cf.get("records") or []
    if isinstance(raw_records, list):
        for item in raw_records:
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain", "") or "").strip()
            if not domain:
                continue
            records.append({
                "domain": domain,
                "zone_id": str(item.get("zone_id", "") or "").strip(),
            })
    if not records:
        domain = str(cf.get("domain", "") or "").strip()
        zone_id = str(cf.get("zone_id", "") or "").strip()
        if domain:
            records.append({"domain": domain, "zone_id": zone_id})
    uniq = []
    seen = set()
    for item in records:
        key = item["domain"].lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


def resolve_zone_id(token, domain, manual_zone_id=""):
    manual_zone_id = str(manual_zone_id or "").strip()
    if manual_zone_id:
        return manual_zone_id
    labels = [x for x in domain.split(".") if x]
    if len(labels) < 2:
        raise RuntimeError(f"域名格式不合法，无法自动识别 Zone ID: {domain}")
    for i in range(len(labels) - 1):
        zone_name = ".".join(labels[i:])
        q = urllib.parse.urlencode({"name": zone_name, "status": "active", "match": "all", "per_page": 1})
        data = cf_request("GET", f"/zones?{q}", token)
        result = data.get("result", []) or []
        if result:
            zone = result[0]
            zid = str(zone.get("id", "") or "").strip()
            zname = str(zone.get("name", "") or "").strip().lower()
            if zid and zname == zone_name.lower():
                return zid
    raise RuntimeError(f"自动获取 Zone ID 失败: {domain}，请检查 Token 权限或域名是否在当前账号下")


def parse_json_lines(path):
    rows = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("ip"):
                rows.append(obj)

    uniq = []
    seen = set()
    for row in rows:
        ip = row.get("ip")
        if ip in seen:
            continue
        seen.add(ip)

        latency = row.get("latency_ms")
        if latency in (None, "", 0, "0"):
            latency = row.get("total_ms") or row.get("ttfb_ms") or row.get("connect_ms")
        try:
            latency = float(latency)
            row["latency_ms"] = latency if latency > 0 else None
        except Exception:
            row["latency_ms"] = None

        row_ok = row.get("ok")
        if isinstance(row_ok, str):
            row_ok = row_ok.strip().lower() in ("1", "true", "yes", "ok")
        row["ok"] = bool(row_ok)

        uniq.append(row)

    dl = [
        r for r in uniq
        if r.get("ok") and float(r.get("download_mbps") or 0) > 0
    ]
    if dl:
        dl.sort(key=lambda x: float(x.get("download_mbps") or 0), reverse=True)
        return "download_mbps", dl

    scored = [
        r for r in uniq
        if r.get("ok") and float(r.get("score_ms") or 0) > 0
    ]
    scored.sort(key=lambda x: float(x.get("score_ms") or 0))
    return "score_ms", scored


def _float_or_none(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def row_loss_rate(row):
    for key in ("loss_rate", "loss", "lossRate", "packet_loss", "packet_loss_rate"):
        val = _float_or_none(row.get(key))
        if val is not None:
            return val / 100.0 if val > 1 else val
    sent = _float_or_none(row.get("sent") or row.get("sended") or row.get("requests"))
    recv = _float_or_none(row.get("received") or row.get("recv") or row.get("success"))
    if sent and sent > 0 and recv is not None:
        return max(0.0, min(1.0, (sent - recv) / sent))
    return None


def row_colo(row):
    for key in ("colo", "cf_colo", "cfcolo", "location", "pop"):
        val = str(row.get(key, "") or "").strip().upper()
        if val:
            return val
    return ""


def apply_result_filters(rows):
    allowed_colos = {x.strip().upper() for x in CF_COLO.split(",") if x.strip()}
    filtered = []
    stats = {"total": len(rows), "latency": 0, "loss": 0, "speed": 0, "colo": 0}
    for row in rows:
        latency = _float_or_none(row.get("latency_ms"))
        if MAX_LATENCY_MS > 0 and latency is not None and latency > MAX_LATENCY_MS:
            stats["latency"] += 1
            continue
        if MIN_LATENCY_MS > 0 and latency is not None and latency < MIN_LATENCY_MS:
            stats["latency"] += 1
            continue
        loss = row_loss_rate(row)
        if MAX_LOSS_RATE < 1 and loss is not None and loss > MAX_LOSS_RATE:
            stats["loss"] += 1
            continue
        speed = _float_or_none(row.get("download_mbps")) or 0
        if MIN_DOWNLOAD_MBPS > 0 and speed < MIN_DOWNLOAD_MBPS:
            stats["speed"] += 1
            continue
        if allowed_colos:
            colo = row_colo(row)
            if not colo or colo not in allowed_colos:
                stats["colo"] += 1
                continue
        filtered.append(row)
    if stats["total"] != len(filtered):
        log(f"[*] 结果过滤: 原始 {stats['total']}，保留 {len(filtered)}，延迟过滤 {stats['latency']}，丢包过滤 {stats['loss']}，速度过滤 {stats['speed']}，地区过滤 {stats['colo']}")
    return filtered


def load_previous_best(mode):
    path = PROJECT_DIR / f"best_ips_v{mode}.json"
    if not path.exists():
        return []
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        rows = obj.get("result", []) or []
        return [r for r in rows if r.get("ip")]
    except Exception:
        return []


def cf_request_all(token, path, result_key="result"):
    items = []
    page = 1
    sep = "&" if "?" in path else "?"
    while True:
        data = cf_request("GET", f"{path}{sep}per_page=100&page={page}", token)
        items.extend(data.get(result_key, []) or [])
        info = data.get("result_info") or {}
        total_pages = int(info.get("total_pages") or 1)
        if page >= total_pages:
            break
        page += 1
    return items


def update_dns_records(token, records, dns_type, best, ttl, proxied):
    desired_ips = []
    seen = set()
    for row in best:
        ip = str(row.get("ip", "") or "").strip()
        if ip and ip not in seen:
            desired_ips.append(ip)
            seen.add(ip)
    if not desired_ips:
        log(f"[!] 没有可写入的 {dns_type} 记录，跳过 DNS 更新，保留现有解析")
        return

    for item in records:
        domain = item["domain"]
        zone_id = resolve_zone_id(token, domain, item.get("zone_id", ""))
        q = urllib.parse.urlencode({"type": dns_type, "name": domain})
        existing = cf_request_all(token, f"/zones/{zone_id}/dns_records?{q}")
        existing = [r for r in existing if str(r.get("type", "")).upper() == dns_type and str(r.get("name", "")).lower() == domain.lower()]
        existing.sort(key=lambda r: str(r.get("created_on") or r.get("id") or ""))

        log(f"[*] 正在差异同步 {domain} 的 {dns_type} 记录：现有 {len(existing)} 条，目标 {len(desired_ips)} 条")
        for idx, ip in enumerate(desired_ips):
            payload = {"type": dns_type, "name": domain, "content": ip, "ttl": ttl, "proxied": proxied}
            if idx < len(existing):
                rec = existing[idx]
                rid = rec.get("id")
                if rec.get("content") == ip and int(rec.get("ttl") or ttl) == int(ttl) and bool(rec.get("proxied", False)) == bool(proxied):
                    continue
                cf_request("PUT", f"/zones/{zone_id}/dns_records/{rid}", token, payload)
            else:
                cf_request("POST", f"/zones/{zone_id}/dns_records", token, payload)

        for rec in existing[len(desired_ips):]:
            rid = rec.get("id")
            if rid:
                cf_request("DELETE", f"/zones/{zone_id}/dns_records/{rid}", token)
        log(f"[✓] {domain} 的 {dns_type} 记录同步完成")


def run_mode(mode, cfg, mcis_bin):
    dns_type = "A" if mode == "4" else "AAAA"
    cidr_file = PROJECT_DIR / ("ipv4cidr.txt" if mode == "4" else "ipv6cidr.txt")
    if not cidr_file.exists():
        log(f"[!] 跳过 IPv{mode}: 缺少 {cidr_file}")
        return

    result_file = PROJECT_DIR / f"scan_results_v{mode}.log"
    host = MCIS_HOST or PROBE_FALLBACK_HOST
    heads = HEADS_V6 if mode == "6" else HEADS_V4
    budget = BUDGET_V6 if mode == "6" else BUDGET
    concurrency = CONCURRENCY_V6 if mode == "6" else CONCURRENCY

    args = [
        str(mcis_bin),
        "-cidr-file", str(cidr_file),
        "-concurrency", concurrency,
        "-top", str(max(TOP_TEST, TOP_N)),
        "-out", "jsonl",
        "-v",
        "-budget", budget,
        "-heads", heads,
        "-host", host,
        "-path", MCIS_PATH,
        "-timeout", TIMEOUT,
        "-rounds", ROUNDS,
    ]
    if int(DOWNLOAD_TOP) > 0:
        args += ["-download-top", DOWNLOAD_TOP, "-download-timeout", DOWNLOAD_TIMEOUT]
        if DOWNLOAD_MODE in ("all", "sequential"):
            args += ["-download-mode", DOWNLOAD_MODE]
        if DOWNLOAD_URL:
            args += ["-download-url", DOWNLOAD_URL]
        if DOWNLOAD_BYTES:
            args += ["-download-bytes", DOWNLOAD_BYTES]

    log(f"[*] 开始优选 IPv{mode}")
    log(f"[*] 并发: {concurrency}，轮次: {ROUNDS}，预算: {budget}，搜索头: {heads}，测速模式: {DOWNLOAD_MODE}")
    log(f"[*] 探测 Host/Path: {host}{MCIS_PATH}")
    with open(result_file, "w", encoding="utf-8") as out:
        try:
            subprocess.run(args, stdout=out, stderr=subprocess.STDOUT, check=True, timeout=MAX_SCAN_SECONDS or None)
        except subprocess.TimeoutExpired:
            log(f"[!] 扫描超时，已按 {MAX_SCAN_SECONDS}s 截止，继续读取已有结果")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"mcis 执行失败，IPv{mode}，退出码: {e.returncode}")

    sort_mode, best = parse_json_lines(result_file)
    best = apply_result_filters(best)[:TOP_N]
    if not best:
        previous = load_previous_best(mode) if KEEP_LAST_ON_FAIL else []
        if previous:
            log(f"[!] IPv{mode} 本次没有找到符合条件的新 IP，已保留旧 DNS 记录和旧结果，共 {len(previous)} 条")
            return
        if KEEP_LAST_ON_FAIL:
            log(f"[!] IPv{mode} 本次没有找到符合条件的新 IP，未更新 DNS")
            return
        raise RuntimeError(f"IPv{mode} 没有找到可用 IP")

    zero_speed = sum(1 for row in best if float(row.get("download_mbps") or 0) <= 0)
    if zero_speed:
        log(f"[!] IPv{mode} 有 {zero_speed} 个结果未测到下载速度，通常表示测速未成功，不一定代表 IP 不可用")

    missing_latency = sum(1 for row in best if not row.get("latency_ms"))
    if missing_latency:
        log(f"[!] IPv{mode} 有 {missing_latency} 个结果缺少延迟字段，已尝试用 total_ms / ttfb_ms / connect_ms 兜底")

    token = str(cfg.get("cloudflare", {}).get("token", "") or "").strip()
    ttl = cfg.get("cloudflare", {}).get("ttl", 60)
    proxied = cfg.get("cloudflare", {}).get("proxied", False)
    records = normalize_records(cfg)
    if not token:
        raise RuntimeError("Cloudflare Token 不能为空")
    if not records:
        raise RuntimeError("至少要配置一个域名")

    update_dns_records(token, records, dns_type, best, ttl, proxied)

    (PROJECT_DIR / f"best_ips_v{mode}.json").write_text(json.dumps({"mode": sort_mode, "result": best}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[✓] IPv{mode} 已同步到 {len(records)} 个域名，排序依据: {sort_mode}")


def main():
    cfg = load_config()
    mcis_bin = ensure_mcis()
    modes = ["4", "6"] if IP_MODE == "both" else [IP_MODE]
    for mode in modes:
        if mode not in ("4", "6"):
            raise RuntimeError(f"无效的 IP_MODE: {IP_MODE}")
        run_mode(mode, cfg, mcis_bin)
    log("[✓] 全部执行完成")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
        print(f"[HTTP 错误] {e} {body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[错误] {e}", file=sys.stderr)
        sys.exit(1)
