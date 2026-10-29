#!/usr/bin/env python3
import errno
import fcntl
import http.client
import io
import ipaddress
import json
import math
import re
import selectors
import tempfile
from collections import Counter
from speed_probe import sample_download, _target as download_target
import os
import shutil
import socket
import ssl
import statistics
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
MCIS_REF = os.getenv("MCIS_REF", "")
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
DOWNLOAD_TIMEOUT = os.getenv("DOWNLOAD_TIMEOUT", "15s")
DOWNLOAD_WINDOW = os.getenv("DOWNLOAD_WINDOW", "6s")
DOWNLOAD_BUDGET = os.getenv("DOWNLOAD_BUDGET", "180s")
DOWNLOAD_MODE = os.getenv("DOWNLOAD_MODE", "sequential")
DOWNLOAD_URL = os.getenv("DOWNLOAD_URL", "")
DOWNLOAD_BYTES = os.getenv("DOWNLOAD_BYTES", "50000000")
MAX_SCAN_SECONDS = int(os.getenv("MAX_SCAN_SECONDS", "300"))
PROBE_FALLBACK_HOST = os.getenv("PROBE_FALLBACK_HOST", "example.com").strip() or "example.com"
HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "4"))
HTTP_RETRY_DELAY = float(os.getenv("HTTP_RETRY_DELAY", "1.5"))
KEEP_LAST_ON_FAIL = os.getenv("KEEP_LAST_ON_FAIL", "true").strip().lower() not in ("0", "false", "no", "off")
MAX_LATENCY_MS = float(os.getenv("MAX_LATENCY_MS", "0") or 0)
MIN_LATENCY_MS = float(os.getenv("MIN_LATENCY_MS", "0") or 0)
MAX_LOSS_RATE = float(os.getenv("MAX_LOSS_RATE", "1") or 1)
MIN_DOWNLOAD_MBPS = float(os.getenv("MIN_DOWNLOAD_MBPS", "0") or 0)
CF_COLO = os.getenv("CF_COLO", "").strip().upper()
SCAN_ONLY = os.getenv("SCAN_ONLY", "false").strip().lower() in ("1", "true", "yes", "on")
SWITCH_MIN_IMPROVEMENT = float(os.getenv("SWITCH_MIN_IMPROVEMENT", "0.20") or 0.20)
SWITCH_MIN_INTERVAL = int(os.getenv("SWITCH_MIN_INTERVAL", "21600") or 21600)
SWITCH_FAILURE_THRESHOLD = int(os.getenv("SWITCH_FAILURE_THRESHOLD", "2") or 2)
SWITCH_RECHECK_ROUNDS = int(os.getenv("SWITCH_RECHECK_ROUNDS", "3") or 3)
SWITCH_RECHECK_TIMEOUT = float(os.getenv("SWITCH_RECHECK_TIMEOUT", "5") or 5)
SWITCH_STATE_LIMIT = 200
LOSS_RECHECK_CANDIDATE_LIMIT = 200
MAX_RECHECK_IPS = 100
MAX_RECHECK_PROBES = 1000
HISTORY_LIMIT = 30


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
        raise RuntimeError("mcis 未初始化，请重新运行安装/更新，或重建并重启容器")
    current_version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else ""
    if MCIS_REF and current_version != f"{MCIS_REF}+source":
        log(f"[!] 当前 mcis 版本标记为: {current_version or 'unknown'}，目标: {MCIS_REF}+source")
    if not mcis_supports_flag(mcis_bin, "-download-mode"):
        raise RuntimeError("当前 mcis 不兼容，请更新原生安装包或容器以初始化内置版本")
    for name in ("ipv4cidr.txt", "ipv6cidr.txt"):
        p = PROJECT_DIR / name
        if not p.exists() or p.stat().st_size == 0:
            raise RuntimeError(f"缺少内置 CIDR 文件: {p}，请重新安装/更新或重启容器完成初始化")
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


_ZONE_CACHE = {}


def resolve_zone_id(token, domain, manual_zone_id=""):
    manual_zone_id = str(manual_zone_id or "").strip()
    if manual_zone_id:
        return manual_zone_id
    cache_key = (token, domain.lower())
    if cache_key in _ZONE_CACHE:
        return _ZONE_CACHE[cache_key]
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
                if len(_ZONE_CACHE) >= 200:
                    _ZONE_CACHE.clear()
                _ZONE_CACHE[cache_key] = zid
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
            if isinstance(obj, dict) and obj.get("ip"):
                rows.append(obj)

    uniq = []
    seen = set()
    for row in rows:
        try:
            ip = str(ipaddress.ip_address(row.get("ip")))
        except (ValueError, TypeError):
            continue
        row["ip"] = ip
        if ip in seen:
            continue
        seen.add(ip)

        trace = row.get("trace") if isinstance(row.get("trace"), dict) else {}
        trace_colo = trace.get("colo")
        if isinstance(trace_colo, str) and trace_colo.strip():
            row["colo"] = trace_colo.strip()

        latency = row.get("latency_ms")
        if latency in (None, "", 0, "0"):
            latency = next((_float_or_none(row.get(key)) for key in ("total_ms", "ttfb_ms", "connect_ms")
                            if (_float_or_none(row.get(key)) or 0) > 0), None)
        else:
            latency = _float_or_none(latency)
        row["latency_ms"] = latency if latency is not None and latency > 0 else None
        # Normalize all numeric metrics before sorting or serializing results.
        for key in ("download_mbps", "score_ms", "total_ms", "ttfb_ms", "tls_ms", "connect_ms",
                    "loss_rate", "loss", "lossRate", "packet_loss", "packet_loss_rate",
                    "sent", "sended", "requests", "received", "recv", "success"):
            if key in row:
                value = _float_or_none(row[key])
                row[key] = value if value is not None and value >= 0 else None
        # A failed/partial download can still report bytes and positive Mbps.
        # Only legacy rows with no status field may infer success from speed.
        if "download_ok" in row:
            row["download_ok"] = _result_bool(row["download_ok"])
            if not row["download_ok"]:
                row["download_mbps"] = None
        # Keep only the public result schema; arbitrary upstream fields may
        # contain non-finite JSON numbers or unexpectedly large nested objects.
        row = {key: row[key] for key in ("ip", "ok", "latency_ms", "connect_ms", "tls_ms", "ttfb_ms", "total_ms", "download_ok", "download_mbps", "score_ms",
               "loss_rate", "loss", "lossRate", "packet_loss", "packet_loss_rate",
               "sent", "sended", "requests", "received", "recv", "success",
               "colo", "cf_colo", "cfcolo", "location", "pop") if key in row}

        row["ok"] = _result_bool(row.get("ok"))
        for key in ("colo", "cf_colo", "cfcolo", "location", "pop"):
            if key in row:
                row[key] = row[key][:64] if isinstance(row[key], str) else ""

        uniq.append(row)

    dl = [
        r for r in uniq
        if r.get("ok") and float(r.get("download_mbps") or 0) > 0
    ]
    scored = [
        r for r in uniq
        if r.get("ok") and float(r.get("score_ms") or 0) > 0
    ]
    scored.sort(key=lambda x: float(x.get("score_ms") or 0))
    if dl:
        dl.sort(key=lambda x: float(x.get("download_mbps") or 0), reverse=True)
        downloaded = {row["ip"] for row in dl}
        # Retain valid HTTP candidates for later filter/backfill. Missing or
        # failed speed stays missing, so an enabled minimum speed rejects it.
        return "download_mbps", dl + [row for row in scored if row["ip"] not in downloaded]
    return "score_ms", scored


def _result_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "ok")
    return value is True or (type(value) in (int, float) and value == 1)


def _float_or_none(value):
    try:
        if value in (None, "") or isinstance(value, bool):
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except Exception:
        return None


def row_loss_rate(row):
    for key in ("loss_rate", "loss", "lossRate", "packet_loss", "packet_loss_rate"):
        val = _float_or_none(row.get(key))
        if val is not None:
            return (val / 100.0 if val > 1 else val) if 0 <= val <= 100 else None
    sent = _float_or_none(row.get("sent") or row.get("sended") or row.get("requests"))
    recv = next((_float_or_none(row[k]) for k in ("received", "recv", "success") if row.get(k) is not None), None)
    if sent and sent > 0 and recv is not None and 0 <= recv <= sent:
        return (sent - recv) / sent
    return None


class _DeadlineRaw(io.RawIOBase):
    """Socket reader that reapplies the absolute deadline on every recv."""
    def __init__(self, sock, prefix, deadline):
        self.sock = sock
        self.prefix = prefix
        self.deadline = deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.prefix:
            size = min(len(buffer), len(self.prefix))
            buffer[:size] = self.prefix[:size]
            self.prefix = self.prefix[size:]
            return size
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("probe deadline exceeded")
        self.sock.settimeout(left)
        return self.sock.recv_into(buffer)


class _PrefixedSocket:
    """Expose an HTTPResponse-compatible deadline-aware byte stream."""
    def __init__(self, sock, prefix, deadline):
        self.sock = sock
        self.prefix = prefix
        self.deadline = deadline

    def makefile(self, mode, buffering=None):
        return io.BufferedReader(_DeadlineRaw(self.sock, self.prefix, self.deadline))


def _probe_target(host, path):
    host = str(host or "").strip()
    path = str(path or "").strip()
    if (not host or any(ch in host for ch in "\r\n") or any(ch.isspace() for ch in host)
            or ":" in host or len(host) > 253):
        raise ValueError("invalid probe host")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("invalid probe host") from exc
    labels = host.rstrip(".").split(".")
    if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
           or not all(c.isalnum() or c == "-" for c in label) for label in labels):
        raise ValueError("invalid probe host")
    if not path.startswith("/") or any(ch in path for ch in "\r\n"):
        raise ValueError("invalid probe path")
    try:
        path.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid probe path") from exc
    return host, path


def direct_https_probe(ip, host, path, timeout=None, ssl_context=None, port=443):
    """Probe one exact IP using verified TLS and a cumulative deadline."""
    timeout = SWITCH_RECHECK_TIMEOUT if timeout is None else float(timeout)
    started = time.monotonic()
    deadline = started + timeout
    raw = tls_sock = None
    try:
        host, path = _probe_target(host, path)
        ip = str(ipaddress.ip_address(ip))
        def remaining():
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("probe deadline exceeded")
            return left

        connect_started = time.monotonic()
        raw = socket.create_connection((ip, int(port)), timeout=remaining())
        connect_ms = (time.monotonic() - connect_started) * 1000
        raw.settimeout(remaining())
        tls_started = time.monotonic()
        context = ssl_context or ssl.create_default_context()
        tls_sock = context.wrap_socket(raw, server_hostname=host)
        raw = None
        tls_ms = (time.monotonic() - tls_started) * 1000
        request = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: cfipup2dns-recheck/2.0\r\n"
                   "Accept: */*\r\nConnection: close\r\n\r\n").encode("ascii")
        tls_sock.settimeout(remaining())
        tls_sock.sendall(request)
        tls_sock.settimeout(remaining())
        first = tls_sock.recv(1)
        if not first:
            raise OSError("empty HTTP response")
        ttfb_ms = (time.monotonic() - started) * 1000
        tls_sock.settimeout(remaining())
        response = http.client.HTTPResponse(_PrefixedSocket(tls_sock, first, deadline))
        response.begin()
        status = response.status
        if not 200 <= status < 300:
            raise OSError(f"HTTP {status}")
        remaining_body = 65536
        while remaining_body:
            tls_sock.settimeout(remaining())
            chunk = response.read(min(8192, remaining_body))
            if not chunk:
                break
            remaining_body -= len(chunk)
        return {"ok": True, "connect_ms": connect_ms, "tls_ms": tls_ms,
                "ttfb_ms": ttfb_ms, "total_ms": (time.monotonic() - started) * 1000,
                "status": status}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
    finally:
        for sock in (tls_sock, raw):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


def recheck_ips(ips, host, path, rounds=None, timeout=None):
    rounds = SWITCH_RECHECK_ROUNDS if rounds is None else int(rounds)
    timeout = SWITCH_RECHECK_TIMEOUT if timeout is None else float(timeout)
    unique_ips = list(dict.fromkeys(ips))
    if not 1 <= rounds <= 20 or not 0.1 <= timeout <= 60:
        raise ValueError("invalid recheck bounds")
    if len(unique_ips) > MAX_RECHECK_IPS or len(unique_ips) * rounds > MAX_RECHECK_PROBES:
        raise ValueError("too many recheck probes")
    results = {}
    for ip in unique_ips:
        samples = [direct_https_probe(ip, host, path, timeout) for _ in range(rounds)]
        good = [s for s in samples if s.get("ok")]
        results[ip] = {
            "attempts": rounds, "successes": len(good),
            "failure_rate": (rounds - len(good)) / rounds,
            "total_ms": statistics.median(s["total_ms"] for s in good) if good else None,
            "connect_ms": statistics.median(s["connect_ms"] for s in good) if good else None,
            "tls_ms": statistics.median(s["tls_ms"] for s in good) if good else None,
            "ttfb_ms": statistics.median(s["ttfb_ms"] for s in good) if good else None,
        }
    return results


def add_per_ip_loss_rechecks(rows, host, path):
    if MAX_LOSS_RATE >= 1 or not rows:
        return rows
    limit = min(len(rows), LOSS_RECHECK_CANDIDATE_LIMIT)
    batch_size = max(1, min(20, TOP_N * 2))
    log(f"[!] mcis JSONL 没有每 IP 丢包指标；失败率改用重复 HTTPS 直连复测（最多 {limit} 个候选），绝不代用 prefix_* 网段计数")
    qualified = []
    for start in range(0, limit, batch_size):
        batch = rows[start:min(limit, start + batch_size)]
        checked = recheck_ips([row["ip"] for row in batch], host, path)
        for row in batch:
            probe = checked.get(row["ip"], {})
            row["loss_rate"] = probe.get("failure_rate")
            row["recheck_attempts"] = probe.get("attempts")
            if row_loss_rate(row) is not None and row_loss_rate(row) <= MAX_LOSS_RATE:
                qualified.append(row)
        if len(qualified) >= TOP_N:
            break
    return qualified[:TOP_N]


def row_colo(row):
    for key in ("colo", "cf_colo", "cfcolo", "location", "pop"):
        val = str(row.get(key, "") or "").strip().upper()
        if val:
            return val
    return ""


def apply_result_filters(rows, skip_loss=False, skip_speed=False):
    allowed_colos = {x.strip().upper() for x in CF_COLO.split(",") if x.strip()}
    filtered = []
    stats = {"total": len(rows), "latency": 0, "loss": 0, "speed": 0, "colo": 0}
    for row in rows:
        latency = _float_or_none(row.get("latency_ms"))
        if MAX_LATENCY_MS > 0 and (latency is None or latency <= 0 or latency > MAX_LATENCY_MS):
            stats["latency"] += 1
            continue
        if MIN_LATENCY_MS > 0 and (latency is None or latency < MIN_LATENCY_MS):
            stats["latency"] += 1
            continue
        loss = row_loss_rate(row)
        if not skip_loss and MAX_LOSS_RATE < 1 and (loss is None or loss > MAX_LOSS_RATE):
            stats["loss"] += 1
            continue
        speed = _float_or_none(row.get("download_mbps")) or 0
        if not skip_speed and MIN_DOWNLOAD_MBPS > 0 and (speed < MIN_DOWNLOAD_MBPS or ("download_ok" in row and not row["download_ok"])):
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


def load_switch_state():
    try:
        data = json.loads((PROJECT_DIR / "switch_state.json").read_text(encoding="utf-8"))
        entries = data.get("entries", {}) if isinstance(data, dict) else {}
        if not isinstance(entries, dict):
            return {"entries": {}}
        clean = {}
        for key, value in list(entries.items())[-SWITCH_STATE_LIMIT:]:
            if isinstance(key, str) and isinstance(value, dict):
                clean[key] = value
        return {"entries": clean}
    except (OSError, ValueError):
        return {"entries": {}}


def save_switch_state(state):
    entries = state.get("entries", {}) if isinstance(state, dict) else {}
    state = {"entries": dict(list(entries.items())[-SWITCH_STATE_LIMIT:])}
    atomic_json_write(PROJECT_DIR / "switch_state.json", state)


def _policy_key(domain, dns_type):
    return f"{domain.lower()}|{dns_type}"


def _safe_nonnegative(value, default=0):
    number = _float_or_none(value)
    return number if number is not None and number >= 0 else default


def decide_switch(domain, dns_type, incumbent_ips, candidate_rows, checks, previous, now=None):
    """Resize and replace DNS slots without deleting unverified incumbents."""
    now = time.time() if now is None else now
    incumbent_ips = list(dict.fromkeys(incumbent_ips))
    candidate_ips = list(dict.fromkeys(r["ip"] for r in candidate_rows if r.get("ip")))
    usable_candidates = []
    for ip in candidate_ips:
        check = checks.get(ip, {}) if isinstance(checks.get(ip, {}), dict) else {}
        attempts = int(_safe_nonnegative(check.get("attempts"), 0))
        successes = int(_safe_nonnegative(check.get("successes"), 0))
        if attempts and successes == attempts:
            usable_candidates.append(ip)
    previous = previous if isinstance(previous, dict) else {}
    old_failure_map = previous.get("failure_counts", {})
    old_failure_map = old_failure_map if isinstance(old_failure_map, dict) else {}
    state = {k: v for k, v in previous.items() if k not in ("failure_counts", "incumbent_failures")}
    state["checked_at"] = now

    if not incumbent_ips:
        if not usable_candidates:
            return incumbent_ips, "bootstrap_no_verified_candidate", state, False
        state["failure_counts"] = {}
        return usable_candidates[:TOP_N], "bootstrap", state, True

    failure_counts = {}
    confirmed_failed, healthy, uncertain = [], [], []
    for ip in incumbent_ips:
        check = checks.get(ip, {})
        attempts = int(_safe_nonnegative(check.get("attempts"), 0))
        successes = int(_safe_nonnegative(check.get("successes"), 0))
        old_count = int(_safe_nonnegative(old_failure_map.get(ip), 0))
        if attempts and successes == 0:
            failure_counts[ip] = old_count + 1
            confirmed_failed.append(ip)
        elif attempts and successes == attempts and _safe_nonnegative(check.get("total_ms"), 0) > 0:
            failure_counts[ip] = 0
            healthy.append(ip)
        else:
            failure_counts[ip] = 0
            uncertain.append(ip)
    state["failure_counts"] = failure_counts

    desired = list(incumbent_ips)
    # Only healthy slots may be removed for a size reduction. Uncertain and
    # failed incumbents retain their existing protection, even above TOP_N.
    excess = max(0, len(desired) - TOP_N)
    removable = [ip for ip in reversed(desired) if ip in healthy][:excess]
    desired = [ip for ip in desired if ip not in removable]
    shrunk = bool(removable)
    shrink_deferred = len(desired) > TOP_N
    changed = False
    available = [ip for ip in usable_candidates if ip not in desired
                 and _safe_nonnegative(checks.get(ip, {}).get("total_ms"), 0) > 0]
    available.sort(key=lambda ip: _safe_nonnegative(checks[ip].get("total_ms"), math.inf))

    # Failed slots are independent, bypass cooldown, and are retained only until
    # their own identity-bound counter reaches the threshold.
    failure_wait = []
    for failed_ip in confirmed_failed:
        count = failure_counts[failed_ip]
        if count < SWITCH_FAILURE_THRESHOLD:
            failure_wait.append(count)
            continue
        if failed_ip in desired and available:
            desired[desired.index(failed_ip)] = available.pop(0)
            changed = True

    # Fill empty slots independently of replacement benefit/cooldown or old
    # failures. Never use expansion as a reason to drop protected incumbents.
    additions = available[:max(0, TOP_N - len(desired))]
    desired.extend(additions)
    if additions:
        reason = "expanded_verified_candidates"
        if changed:
            reason += ";incumbent_failure_threshold"
        return desired, reason, state, True
    if shrunk:
        reason = "shrunk_healthy_surplus"
        if shrink_deferred:
            reason += ";shrink_deferred_protected_incumbents"
        if changed:
            reason += ";incumbent_failure_threshold"
        return desired, reason, state, True
    if changed:
        reason = "incumbent_failure_threshold"
        if shrink_deferred:
            reason += ";shrink_deferred_protected_incumbents"
        return desired, reason, state, True
    if shrink_deferred:
        return desired, "shrink_deferred_protected_incumbents", state, False
    if failure_wait:
        return desired, f"incumbent_failure_wait_{max(failure_wait)}/{SWITCH_FAILURE_THRESHOLD}", state, False
    if any(failure_counts[ip] >= SWITCH_FAILURE_THRESHOLD for ip in confirmed_failed):
        return desired, "incumbent_failed_no_verified_candidate", state, False

    # A soft error is uncertainty, never evidence for a normal replacement.
    if uncertain:
        return desired, "recheck_uncertain_keep", state, False

    # Normal replacement pairs fastest candidates with the slowest healthy
    # incumbent slots. Matching incumbents stay in place and slower candidates
    # are never swept in merely because one candidate is excellent.
    if not available:
        return desired, "incumbent_still_best", state, False
    last_switch = _safe_nonnegative(previous.get("last_switch_at"), 0)
    if last_switch and now - last_switch < SWITCH_MIN_INTERVAL:
        return desired, "cooldown", state, False
    replacements = []
    healthy_slowest = sorted((ip for ip in healthy if ip in desired),
                             key=lambda ip: _safe_nonnegative(checks[ip].get("total_ms"), 0), reverse=True)
    for incumbent_ip, candidate_ip in zip(healthy_slowest, available):
        incumbent_ms = _safe_nonnegative(checks[incumbent_ip].get("total_ms"), 0)
        candidate_ms = _safe_nonnegative(checks[candidate_ip].get("total_ms"), 0)
        improvement = (incumbent_ms - candidate_ms) / incumbent_ms if incumbent_ms else 0
        # Even a configured zero threshold requires a strict actual improvement.
        if candidate_ms < incumbent_ms and improvement >= SWITCH_MIN_IMPROVEMENT:
            replacements.append((incumbent_ip, candidate_ip, improvement))
    if not replacements:
        return desired, "benefit_too_small", state, False
    for incumbent_ip, candidate_ip, _ in replacements:
        desired[desired.index(incumbent_ip)] = candidate_ip
    state["last_improvement"] = max(item[2] for item in replacements)
    return desired, "minimum_improvement_met", state, True


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


def update_dns_records(token, records, dns_type, best, ttl, proxied, probe_host=None, probe_path=MCIS_PATH):
    desired_ips = []
    seen = set()
    for row in best:
        ip = str(row.get("ip", "") or "").strip()
        if not ip:
            continue
        address = ipaddress.ip_address(ip)
        if address.version != (4 if dns_type == "A" else 6):
            raise ValueError(f"IP 类型与 {dns_type} 不匹配: {ip}")
        ip = str(address)
        if ip not in seen:
            desired_ips.append(ip)
            seen.add(ip)
    if not desired_ips:
        log(f"[!] 没有可写入的 {dns_type} 记录，跳过 DNS 更新，保留现有解析")
        return

    outcomes = []
    switch_state = load_switch_state() if probe_host else None
    for item in records:
        domain = item["domain"]
        reason = "policy_not_evaluated"
        try:
            zone_id = resolve_zone_id(token, domain, item.get("zone_id", ""))
            q = urllib.parse.urlencode({"type": dns_type, "name": domain})
            existing = cf_request_all(token, f"/zones/{zone_id}/dns_records?{q}")
            existing = [r for r in existing if str(r.get("type", "")).upper() == dns_type and str(r.get("name", "")).lower() == domain.lower()]
            existing.sort(key=lambda r: str(r.get("created_on") or r.get("id") or ""))

            desired_for_domain = list(desired_ips)
            reason, pending_state, switched = "policy_disabled", None, True
            if probe_host:
                incumbent_ips = []
                for rec in existing:
                    try:
                        incumbent_ips.append(str(ipaddress.ip_address(rec.get("content", ""))))
                    except ValueError:
                        continue
                checks = recheck_ips(incumbent_ips + desired_ips, probe_host, probe_path)
                previous = switch_state["entries"].get(_policy_key(domain, dns_type), {})
                desired_for_domain, reason, pending_state, switched = decide_switch(
                    domain, dns_type, incumbent_ips, best, checks, previous)
                log(f"[*] {domain} {dns_type} 切换策略: {reason}；现有 {incumbent_ips or '无'}；目标 {desired_for_domain or '无'}")
                if not desired_for_domain:
                    outcomes.append({"domain": domain, "status": "failed", "reason": reason})
                    continue

            log(f"[*] 正在差异同步 {domain} 的 {dns_type} 记录：现有 {len(existing)} 条，目标 {len(desired_for_domain)} 条")
            remaining = list(existing)
            matched = {}
            for ip in desired_for_domain:
                rec = next((r for r in remaining if r.get("content") == ip), None)
                if rec is not None:
                    matched[ip] = rec
                    remaining.remove(rec)
            for ip in desired_for_domain:
                payload = {"type": dns_type, "name": domain, "content": ip, "ttl": ttl, "proxied": proxied}
                rec = matched.get(ip)
                if rec is None and remaining:
                    rec = remaining.pop(0)
                if rec is not None:
                    rid = rec["id"]
                    effective_ttl = 1 if proxied else ttl
                    if rec.get("content") == ip and int(rec.get("ttl") or ttl) == int(effective_ttl) and bool(rec.get("proxied", False)) == bool(proxied):
                        continue
                    cf_request("PATCH", f"/zones/{zone_id}/dns_records/{rid}", token, payload)
                else:
                    cf_request("POST", f"/zones/{zone_id}/dns_records", token, payload)

            for rec in remaining:
                rid = rec.get("id")
                if rid:
                    cf_request("DELETE", f"/zones/{zone_id}/dns_records/{rid}", token)
            actual = cf_request_all(token, f"/zones/{zone_id}/dns_records?{q}")
            actual = [r for r in actual if str(r.get("type", "")).upper() == dns_type and str(r.get("name", "")).lower() == domain.lower()]
            actual_ips = [str(ipaddress.ip_address(r.get("content", ""))) for r in actual]
            effective_ttl = 1 if proxied else ttl
            if (sorted(actual_ips) != sorted(desired_for_domain)
                    or any(r.get("ttl") != effective_ttl or r.get("proxied") != proxied for r in actual)):
                raise RuntimeError("DNS API 回读与目标不一致")
            log(f"[✓] {domain} 的 {dns_type} 记录 API 回读验证通过")
            if switch_state is not None and pending_state is not None:
                pending_state["last_reason"] = reason
                pending_state["actual_ips"] = actual_ips
                if switched:
                    pending_state["last_switch_at"] = time.time()
                # Save a detached snapshot. A failed write must not pollute the
                # in-memory state that a later successful domain persists.
                next_state = {"entries": dict(switch_state["entries"])}
                next_state["entries"][_policy_key(domain, dns_type)] = pending_state
                save_switch_state(next_state)
                switch_state = next_state
            outcomes.append({"domain": domain, "status": "verified", "reason": reason,
                             "switched": switched, "actual_ips": actual_ips})
        except Exception as exc:
            log(f"[!] {domain} 的 {dns_type} 同步/回读失败 ({type(exc).__name__})，可能已部分写入；继续其他域名")
            outcomes.append({"domain": domain, "status": "failed",
                             "reason": "dns_sync_or_readback_failed",
                             "policy_reason": reason,
                             "error": type(exc).__name__})
    return outcomes


def ipv6_network_available():
    """Check local IPv6 route/source selection, without sending any packets.

    Unknown errors are not evidence of missing IPv6; in particular an external
    timeout must never suppress scanning. This runs in the runner's own netns.
    """
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
            sock.connect(("2606:4700:4700::1111", 443))
        return True
    except OSError as exc:
        return exc.errno not in (errno.ENETUNREACH, errno.EADDRNOTAVAIL,
                                 errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.ENODEV)


def duration_seconds(value):
    text = str(value).strip()
    text = text[1:] if text.startswith('+') else text
    parts = list(re.finditer(r'((?:\d+(?:\.\d*)?|\.\d+))(ns|us|µs|ms|s|m|h)?', text))
    if not parts or ''.join(p.group(0) for p in parts) != text:
        raise ValueError('invalid duration')
    seconds = sum(float(p.group(1)) * {'ns': 1e-9, 'us': 1e-6, 'µs': 1e-6, 'ms': .001, 's': 1, 'm': 60, 'h': 3600, None: 1}[p.group(2)] for p in parts)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('invalid duration')
    return seconds


def run_mcis(args, result_file, mode, timeout=None):
    """Drain merged output without reader threads; preserve inherited PGID.

    Tee raw output into the private JSONL file, but expose only sanitized
    numeric progress on stdout: arbitrary child diagnostics may contain URLs
    or secrets. No new session/group: the WebUI's group kill reaches MCIS too.
    """
    deadline = time.monotonic() + timeout if timeout else None
    last_progress = 0.0
    total = complete = 0
    scanned = scan_budget = 0
    pending = b''
    timed_out = False
    with os.fdopen(os.open(result_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'wb') as out:
        os.fchmod(out.fileno(), 0o600)
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if deadline is not None and time.monotonic() >= deadline:
                        timed_out = True
                        proc.kill()
                        # Kill closes the writer, then preserve complete rows
                        # already buffered before the deadline.
                        proc.wait(timeout=5)
                        os.set_blocking(proc.stdout.fileno(), False)
                        drain_deadline = time.monotonic() + 1
                        while time.monotonic() < drain_deadline:
                            try:
                                buffered = os.read(proc.stdout.fileno(), 65536)
                            except BlockingIOError:
                                break
                            if not buffered:
                                break
                            out.write(buffered)
                            total += len(buffered)
                        out.flush()
                        break
                    for key, _ in selector.select(.1):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        out.write(chunk)
                        out.flush()
                        total += len(chunk)
                        pending += chunk
                        # Bound the progress parser even for huge/no-newline output.
                        lines = pending.replace(b'\r', b'\n').split(b'\n')
                        pending = lines.pop()[-4096:]
                        for line in lines:
                            progress = re.match(rb'progress:\s*(\d+)/(\d+)\s+done', line)
                            if progress:
                                scanned, scan_budget = (int(value) for value in progress.groups())
                            if len(line) <= 65536:
                                try:
                                    row = json.loads(line)
                                    if isinstance(row, dict) and row.get('ip'):
                                        complete += 1
                                except (ValueError, UnicodeError):
                                    pass
                    if time.monotonic() - last_progress >= 1:
                        log(f'[*] IPv{mode} 扫描进度 MCIS progress: {scanned}/{scan_budget} output_bytes={total}, completed_rows={complete}')
                        last_progress = time.monotonic()
                # A child may close stdout before exiting; deadline still applies.
                left = max(.001, deadline - time.monotonic()) if deadline else None
                try:
                    code = proc.wait(timeout=left)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    proc.kill()
                    code = proc.wait(timeout=5)
            if timed_out:
                log(f'[!] IPv{mode} MCIS deadline={timeout}s; 读取已完成 JSONL')
            elif code:
                raise RuntimeError(f'mcis 执行失败，IPv{mode}，退出码: {code}')
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            proc.stdout.close()
    log(f'[*] IPv{mode} MCIS complete: output_bytes={total}, completed_rows={complete}, timeout={timed_out}')
    return {'scan_timed_out': timed_out}


def sample_candidates(rows, mode, host, path):
    configured = int(DOWNLOAD_TOP)
    if configured < 0:
        raise ValueError('invalid DOWNLOAD_TOP')
    rows = apply_result_filters(rows, skip_loss=MAX_LOSS_RATE < 1, skip_speed=True)
    limit = min(len(rows), 50, max(configured, TOP_N * 2)) if configured else 0
    rows = rows[:limit if configured else min(50, TOP_N * 2)]
    window = duration_seconds(DOWNLOAD_WINDOW)
    budget = duration_seconds(DOWNLOAD_BUDGET)
    cap = min(duration_seconds(DOWNLOAD_TIMEOUT), window + 9)
    max_bytes = int(DOWNLOAD_BYTES)
    if not 1024 <= max_bytes <= 1000000000:
        raise ValueError('invalid DOWNLOAD_BYTES')
    download_target(DOWNLOAD_URL)  # Reject unsafe URLs before any network activity.
    deadline = time.monotonic() + budget
    attempts = successes = qualified_count = 0
    errors = Counter()
    sampled = []
    fallback = []
    budget_expired = False
    log(f'[*] IPv{mode} download start: serial window={window:g}s budget={budget:g}s effective_timeout_cap={cap:g}s attempts<={limit} target={TOP_N}')
    for index, original in enumerate(rows):
        row = dict(original)
        # Never reuse MCIS download metrics: built-in downloads are disabled.
        for key in ('download_mbps', 'download_bytes', 'download_ms'):
            row.pop(key, None)
        row.update(download_ok=False, download_method='not_sampled', download_source='mcis_http',
                   download_error='', download_stop_reason='not_sampled')
        left = deadline - time.monotonic()
        if left <= 0:
            budget_expired = True
        target_reached = DOWNLOAD_MODE == 'sequential' and qualified_count >= TOP_N
        if attempts >= limit or budget_expired or target_reached:
            row['download_stop_reason'] = ('disabled' if not configured else 'budget' if budget_expired
                                           else 'target_reached' if target_reached else 'attempt_limit')
            fallback.append(row)
            continue
        # Filter cheap non-speed requirements before spending a sample slot.
        if not apply_result_filters([row], skip_loss=MAX_LOSS_RATE < 1, skip_speed=True):
            continue
        attempts += 1
        log(f'[*] IPv{mode} download attempt={attempts}/{limit} ip={row["ip"]} budget_left={left:.1f}s')
        measured_at = time.time()
        result = sample_download(row['ip'], DOWNLOAD_URL, window_seconds=window,
                                 timeout_seconds=min(cap, left), max_bytes=max_bytes)
        row.update({k: v for k, v in result.items() if k.startswith('download_')})
        row.update(download_method='windowed_http', download_source='direct_ip', download_measured_at=measured_at)
        if result['download_ok']:
            successes += 1
        else:
            errors[result.get('download_error') or 'unknown'] += 1
        speed_text = f'{result["download_mbps"]:.2f} Mbps' if result.get('download_ok') and result.get('download_mbps') is not None else '无有效速度'
        log(f'[*] IPv{mode} 下载进度 download progress: {attempts}/{limit} success={successes} ip={row["ip"]} speed={speed_text} bytes={result["download_bytes"]} stop={result["download_stop_reason"]}')
        eligible = apply_result_filters([row], skip_loss=MAX_LOSS_RATE < 1)
        if eligible:
            eligible = add_per_ip_loss_rechecks(eligible, host, path)
            eligible = apply_result_filters(eligible)
        if eligible:
            sampled.append(row)
            if result['download_ok']:
                qualified_count += 1
    if time.monotonic() >= deadline:
        budget_expired = True
    sampled.sort(key=lambda r: (not r.get('download_ok'), -float(r.get('download_mbps') or 0), float(r.get('score_ms') or math.inf)))
    remaining = apply_result_filters(fallback, skip_loss=MAX_LOSS_RATE < 1)
    if len(sampled) < TOP_N:
        remaining = add_per_ip_loss_rechecks(remaining, host, path)
        sampled.extend(apply_result_filters(remaining))
    selected = sampled[:TOP_N]
    if attempts and not successes:
        log(f'[!] IPv{mode} download all failed: ' + ', '.join(f'{k}={v}' for k, v in sorted(errors.items())))
    log(f'[*] IPv{mode} download complete: attempts={attempts}/{limit} success={successes} qualified={qualified_count} budget_expired={budget_expired}')
    return selected, {'download_target_count': limit, 'download_attempt_count': attempts,
                      'download_success_count': successes, 'download_qualified_count': qualified_count,
                      'download_budget_expired': budget_expired, 'download_errors': dict(errors)}


def run_mode(mode, cfg, mcis_bin):
    dns_type = "A" if mode == "4" else "AAAA"
    cidr_file = PROJECT_DIR / ("ipv4cidr.txt" if mode == "4" else "ipv6cidr.txt")
    if not cidr_file.exists():
        log(f"[!] 跳过 IPv{mode}: 缺少 {cidr_file}")
        return {"mode": mode, "status": "failed", "reason": "missing_cidr"}

    if mode == "6" and not ipv6_network_available():
        log(f"[!] IPv6 network_unavailable：本机无可用 IPv6 路由/源地址，跳过扫描，保留旧 DNS 和旧结果；请求 {TOP_N}，选中 0")
        return {"mode": mode, "status": "network_unavailable",
                "reason": "ipv6_no_local_route_kept_old", "requested_count": TOP_N, "selected_count": 0}

    result_file = PROJECT_DIR / f"scan_results_v{mode}.log"
    host = MCIS_HOST or PROBE_FALLBACK_HOST
    host, probe_path = _probe_target(host, MCIS_PATH)
    heads = HEADS_V6 if mode == "6" else HEADS_V4
    budget = BUDGET_V6 if mode == "6" else BUDGET
    concurrency = CONCURRENCY_V6 if mode == "6" else CONCURRENCY
    configured_download_top = int(DOWNLOAD_TOP)
    if configured_download_top < 0:
        raise ValueError("无效 DOWNLOAD_TOP")
    download_top = min(50, max(configured_download_top, TOP_N * 2)) if configured_download_top else 0
    candidate_top = min(10000, max(TOP_TEST, TOP_N, download_top))

    args = [
        str(mcis_bin),
        "-cidr-file", str(cidr_file),
        "-concurrency", concurrency,
        "-top", str(candidate_top),
        "-out", "jsonl",
        "-v",
        "-budget", budget,
        "-heads", heads,
        "-host", host,
        "-path", probe_path,
        "-timeout", TIMEOUT,
        "-rounds", ROUNDS,
    ]
    # Explicit zero is required: mcis otherwise defaults to five downloads.
    args += ["-download-top", "0"]

    log(f"[*] 开始优选 IPv{mode}")
    log(f"[*] 并发: {concurrency}，轮次: {ROUNDS}，预算: {budget}，搜索头: {heads}，测速模式: {DOWNLOAD_MODE}")
    log(f"[*] 探测 Host/Path: {host}{probe_path}")
    log(f"[*] 请求数量 {TOP_N}，候选池上限 {candidate_top}，下载候选目标 {download_top}（用户配置 {configured_download_top}）；目标非成功保证，受扫描结果/超时/过滤约束")
    scan_meta = run_mcis(args, result_file, mode, MAX_SCAN_SECONDS or None)
    sort_mode, best = parse_json_lines(result_file)
    best = [row for row in best if ipaddress.ip_address(row["ip"]).version == int(mode)]
    candidate_count = len(best)
    best, download_counts = sample_candidates(best, mode, host, probe_path)
    sort_mode = 'download_mbps' if any(r.get('download_ok') for r in best) else 'score_ms'
    counts = {"requested_count": TOP_N, "candidate_count": candidate_count,
              **download_counts, **scan_meta, "selected_count": len(best)}
    download_success_count = download_counts['download_success_count']
    log(f"[*] IPv{mode} 数量汇总：请求 {TOP_N}，有效候选 {candidate_count}，有有效下载速度 {download_success_count}，过滤后选中 {len(best)}")
    if not best:
        previous = load_previous_best(mode) if KEEP_LAST_ON_FAIL else []
        if previous:
            log(f"[!] IPv{mode} 本次没有找到符合条件的新 IP，已保留旧 DNS 记录和旧结果，共 {len(previous)} 条")
            return {"mode": mode, "status": "kept_old", **counts}
        if KEEP_LAST_ON_FAIL:
            log(f"[!] IPv{mode} 本次没有找到符合条件的新 IP，未更新 DNS")
            return {"mode": mode, "status": "no_results", **counts}
        raise RuntimeError(f"IPv{mode} 没有找到可用 IP")

    zero_speed = sum(1 for row in best if float(row.get("download_mbps") or 0) <= 0)
    if zero_speed:
        log(f"[!] IPv{mode} 有 {zero_speed} 个结果没有有效下载速度（未执行或未成功），不作为已测速结果，不一定代表 IP 不可用")

    missing_latency = sum(1 for row in best if not row.get("latency_ms"))
    if missing_latency:
        log(f"[!] IPv{mode} 有 {missing_latency} 个结果缺少延迟字段，已尝试用 total_ms / ttfb_ms / connect_ms 兜底")

    if SCAN_ONLY:
        atomic_json_write(PROJECT_DIR / f"scan_only_v{mode}.json", {"mode": sort_mode, "result": best})
        log(f"[✓] IPv{mode} 仅测速完成，未调用 DNS API，未覆盖已同步结果")
        return {"mode": mode, "status": "scan_only", **counts}

    token = str(cfg.get("cloudflare", {}).get("token", "") or "").strip()
    ttl = cfg.get("cloudflare", {}).get("ttl", 60)
    proxied = cfg.get("cloudflare", {}).get("proxied", False)
    records = normalize_records(cfg)
    if not token:
        raise RuntimeError("Cloudflare Token 不能为空")
    if not records:
        raise RuntimeError("至少要配置一个域名")

    domains = update_dns_records(token, records, dns_type, best, ttl, proxied, host, probe_path)
    verified = sum(item["status"] == "verified" for item in domains)
    status = "verified" if verified == len(domains) else ("partial_failure" if verified else "failed")
    if status == "verified":
        by_ip = {row["ip"]: row for row in best}
        synced = []
        seen_ips = set()
        for outcome in domains:
            for ip in outcome.get("actual_ips", []):
                if ip not in seen_ips:
                    synced.append(by_ip.get(ip, {"ip": ip, "ok": True}))
                    seen_ips.add(ip)
        atomic_json_write(PROJECT_DIR / f"best_ips_v{mode}.json", {"mode": sort_mode, "result": synced})
    log(f"[*] IPv{mode} DNS 验证通过 {verified}/{len(domains)}，状态: {status}")
    return {"mode": mode, "status": status, **counts, "domains": domains}


def atomic_json_write(path, data):
    fd, name = tempfile.mkstemp(prefix=".result-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    # A process-wide lock also covers direct CLI invocations, not just WebUI jobs.
    with open(PROJECT_DIR / ".run.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("已有优选任务运行中")
        return run_all_modes()


def run_all_modes():
    started = time.time()
    results = []
    try:
        for name, value in (("MAX_LATENCY_MS", MAX_LATENCY_MS), ("MIN_LATENCY_MS", MIN_LATENCY_MS),
                            ("MAX_LOSS_RATE", MAX_LOSS_RATE), ("MIN_DOWNLOAD_MBPS", MIN_DOWNLOAD_MBPS),
                            ("SWITCH_MIN_IMPROVEMENT", SWITCH_MIN_IMPROVEMENT),
                            ("SWITCH_RECHECK_TIMEOUT", SWITCH_RECHECK_TIMEOUT)):
            if not math.isfinite(value) or value < 0 or (name == "MAX_LOSS_RATE" and value > 1):
                raise ValueError(f"无效 {name}")
        if MAX_SCAN_SECONDS < 0:
            raise ValueError('invalid MAX_SCAN_SECONDS')
        duration_seconds(DOWNLOAD_WINDOW)
        duration_seconds(DOWNLOAD_BUDGET)
        duration_seconds(DOWNLOAD_TIMEOUT)
        if MAX_LATENCY_MS and MIN_LATENCY_MS > MAX_LATENCY_MS:
            raise ValueError("最小延迟不能大于最大延迟")
        if (SWITCH_MIN_IMPROVEMENT > 1 or SWITCH_MIN_INTERVAL < 0 or SWITCH_FAILURE_THRESHOLD < 1
                or not 1 <= SWITCH_RECHECK_ROUNDS <= 20 or not 0.1 <= SWITCH_RECHECK_TIMEOUT <= 60
                or not 1 <= TOP_N <= 50 or not 1 <= TOP_TEST <= 10000
                or TOP_N * SWITCH_RECHECK_ROUNDS > 1000):
            raise ValueError("无效 DNS 切换策略参数")
        cfg = {} if SCAN_ONLY else load_config()
        mcis_bin = ensure_mcis()
        modes = ["4", "6"] if IP_MODE == "both" else [IP_MODE]
        for mode in modes:
            try:
                if mode not in ("4", "6"):
                    raise ValueError("无效 IP_MODE")
                results.append(run_mode(mode, cfg, mcis_bin))
            except Exception as exc:
                log(f"[!] IPv{mode} 失败: {type(exc).__name__}")
                results.append({"mode": mode, "status": "failed", "error": type(exc).__name__})
    except Exception as exc:
        log(f"[!] 初始化失败: {type(exc).__name__}")
        results.append({"status": "failed", "error": type(exc).__name__})
    statuses = {r["status"] for r in results}
    if statuses <= {"verified", "scan_only"}:
        status, code = ("scan_only" if SCAN_ONLY else "verified"), 0
    elif statuses & {"failed", "partial_failure", "network_unavailable"}:
        status, code = ("partial_failure" if statuses & {"verified", "scan_only", "partial_failure"} else "failed"), 1
    else:
        status, code = "no_new_results", 2
    summary = {"started_at": started, "ended_at": time.time(), "scan_only": SCAN_ONLY,
               "status": status, "code": code, "results": results,
               "requested_count": TOP_N, "selected_count": sum(r.get("selected_count", 0) for r in results)}
    history_path = PROJECT_DIR / "run_history.json"
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
        if not isinstance(history, list):
            history = []
    except (OSError, ValueError):
        history = []
    atomic_json_write(history_path, (history + [summary])[-HISTORY_LIMIT:])
    log(f"[*] 本次执行结束: {status}，退出码 {code}，每模式请求 {TOP_N}，总选中 {summary['selected_count']}；" +
        "；".join(f"IPv{r.get('mode', '?')}={r['status']}({r.get('selected_count', 0)})" for r in results))
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
        print(f"[HTTP 错误] {e} {body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[错误] {e}", file=sys.stderr)
        sys.exit(1)
