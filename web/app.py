#!/usr/bin/env python3
import base64
import csv
import hmac
import html
import io
import ipaddress
import json
import math
import re
import signal
import os
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PROJECT_DIR = Path(os.getenv("PROJECT_DIR", "/data/project"))
CONFIG_FILE = Path(os.getenv("CONFIG_FILE", str(PROJECT_DIR / "config.json")))
CRON_FILE = Path(os.getenv("CRON_FILE", "/data/cron/cfip.cron"))
LOG_FILE = Path(os.getenv("LOG_FILE", "/data/logs/cron.log"))
INIT_LOG_FILE = Path(os.getenv("INIT_LOG_FILE", "/data/logs/init-mcis.log"))
BOOT_LOG_FILE = Path(os.getenv("BOOT_LOG_FILE", "/data/logs/boot.log"))
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(2 * 1024 * 1024)))
LOG_KEEP_BYTES = int(os.getenv("LOG_KEEP_BYTES", str(512 * 1024)))
LOG_API_TAIL_LINES = int(os.getenv("LOG_API_TAIL_LINES", "300"))
LOG_MEMORY_MAX_BYTES = int(os.getenv("LOG_MEMORY_MAX_BYTES", "16000"))
PORT = int(os.getenv("PORT", "9527"))
WEB_BIND_HOST = os.getenv("WEB_BIND_HOST", "0.0.0.0")
RUN_CMD = os.getenv("RUN_CMD", "/opt/cfipup2dns/cfip.sh")
BEST_IP_FILES = {
    "4": PROJECT_DIR / "best_ips_v4.json",
    "6": PROJECT_DIR / "best_ips_v6.json",
}

# The reservation, process creation and cancellation share one lock.
RUN_LOCK = threading.RLock()
CONFIG_LOCK = threading.RLock()
RUN_PROCESS = None
RUN_THREAD = None
MAX_REQUEST_BYTES = 64 * 1024

LAST_RUN = {
    "cancelled": False,
    "running": False,
    "code": None,
    "stdout": "",
    "stderr": "",
    "started_at": None,
    "ended_at": None,
    "selected_ips": [],
}

DNS_CACHE = {
    "items": [],
    "updated_at": None,
    "fetched_at": None,
    "error": None,
}
DNS_CACHE_TTL = int(os.getenv("DNS_CACHE_TTL", "120"))
RUN_SETTINGS_DEFAULTS = {"ip_mode": "both", "top_n": 5}
ADVANCED_DEFAULTS = {
    "select_profile": os.getenv("SELECT_PROFILE", "throughput"),
    "candidate_pool": os.getenv("CANDIDATE_POOL", "200"),
    "download_qualify_mbps": os.getenv("DOWNLOAD_QUALIFY_MBPS", "10"),
    "download_recheck_rounds": os.getenv("DOWNLOAD_RECHECK_ROUNDS", "2"),
    "switch_download_budget": os.getenv("SWITCH_DOWNLOAD_BUDGET", "180s"),
    "timeout": os.getenv("TIMEOUT", "6s"),
    "rounds": os.getenv("ROUNDS", "6"),
    "download_top": os.getenv("DOWNLOAD_TOP", "8"),
    "download_timeout": os.getenv("DOWNLOAD_TIMEOUT", "15s"),
    "download_window": os.getenv("DOWNLOAD_WINDOW", "6s"),
    "download_budget": os.getenv("DOWNLOAD_BUDGET", "180s"),
    "max_scan_seconds": os.getenv("MAX_SCAN_SECONDS", "300"),
    "download_mode": os.getenv("DOWNLOAD_MODE", "sequential"),
    "download_url": os.getenv("DOWNLOAD_URL", ""),
    "min_download_mbps": os.getenv("MIN_DOWNLOAD_MBPS", "0"),
    "max_latency_ms": os.getenv("MAX_LATENCY_MS", "0"),
    "min_latency_ms": os.getenv("MIN_LATENCY_MS", "0"),
    "max_loss_rate": os.getenv("MAX_LOSS_RATE", "1"),
    "cf_colo": os.getenv("CF_COLO", ""),
    "keep_last_on_fail": os.getenv("KEEP_LAST_ON_FAIL", "true"),
    "switch_min_improvement": os.getenv("SWITCH_MIN_IMPROVEMENT", "0.20"),
    "switch_min_interval": os.getenv("SWITCH_MIN_INTERVAL", "21600"),
    "switch_failure_threshold": os.getenv("SWITCH_FAILURE_THRESHOLD", "2"),
    "switch_recheck_rounds": os.getenv("SWITCH_RECHECK_ROUNDS", "3"),
    "switch_recheck_timeout": os.getenv("SWITCH_RECHECK_TIMEOUT", "5"),
}


def _cf_request(method, path, token):
    url = f"https://api.cloudflare.com/client/v4{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "cfipup2dns-web/2.0",
    }
    req = urllib.request.Request(url, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        obj = json.loads(resp.read().decode("utf-8"))
    if not obj.get("success"):
        raise RuntimeError(json.dumps(obj, ensure_ascii=False))
    return obj


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


def parse_records_text(text):
    records = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "|" in line:
            domain, zone_id = line.split("|", 1)
            records.append({"domain": domain.strip(), "zone_id": zone_id.strip()})
        else:
            records.append({"domain": line, "zone_id": ""})
    uniq = []
    seen = set()
    for item in records:
        domain = str(item.get("domain", "") or "").strip()
        if not domain:
            continue
        key = domain.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append({"domain": domain, "zone_id": str(item.get("zone_id", "") or "").strip()})
    return uniq


def records_to_text(records):
    lines = []
    for item in records or []:
        domain = str(item.get("domain", "") or "").strip()
        zone_id = str(item.get("zone_id", "") or "").strip()
        if not domain:
            continue
        lines.append(f"{domain}|{zone_id}" if zone_id else domain)
    return "\n".join(lines)


def _load_best_ips_from_file():
    items = []
    updated_at = None
    for version, path in BEST_IP_FILES.items():
        if not path.exists():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("result", []), list):
            continue
        for row in obj.get("result", []) or []:
            if not isinstance(row, dict):
                continue
            ip = row.get("ip")
            if not ip:
                continue
            normalized = normalize_ip_row(version, row)
            normalized["result_updated_at"] = path.stat().st_mtime
            items.append(normalized)
        ts = path.stat().st_mtime
        if updated_at is None or ts > updated_at:
            updated_at = ts
    return {"items": items, "updated_at": updated_at}


def _load_best_ips_from_dns():
    cfg = load_config()
    cf = cfg.get("cloudflare", {}) if isinstance(cfg, dict) else {}
    token = str(cf.get("token", "") or "").strip()
    records = normalize_records(cfg)
    if not token or not records:
        return {"items": [], "updated_at": None}
    items = []
    updated_at = None
    for item in records:
        zone_id = str(item.get("zone_id", "") or "").strip()
        domain = str(item.get("domain", "") or "").strip()
        if not zone_id or not domain:
            continue
        for dns_type, version in (("A", "4"), ("AAAA", "6")):
            q = urllib.parse.urlencode({"type": dns_type, "name": domain})
            data = _cf_request("GET", f"/zones/{zone_id}/dns_records?{q}", token)
            for rec in data.get("result", []) or []:
                ip = rec.get("content")
                if not ip:
                    continue
                items.append({
                    "version": version,
                    "ip": ip,
                    "latency_ms": None,
                    "download_mbps": None,
                })
                modified = rec.get("modified_on") or rec.get("created_on")
                if modified:
                    try:
                        ts = datetime.fromisoformat(modified.replace("Z", "+00:00")).timestamp()
                        if updated_at is None or ts > updated_at:
                            updated_at = ts
                    except Exception:
                        pass
    return {"items": items, "updated_at": updated_at}


def get_selected_ips():
    now = time.time()
    if DNS_CACHE["fetched_at"] and now - DNS_CACHE["fetched_at"] < DNS_CACHE_TTL:
        return {"items": DNS_CACHE["items"], "updated_at": DNS_CACHE["updated_at"], "error": DNS_CACHE["error"]}
    data = _load_best_ips_from_file()
    if not data["items"]:
        try:
            data = _load_best_ips_from_dns()
            DNS_CACHE["error"] = None
        except Exception as e:
            DNS_CACHE["error"] = str(e)
    DNS_CACHE.update({
        "items": data["items"],
        "updated_at": data["updated_at"],
        "fetched_at": now,
    })
    return {"items": DNS_CACHE["items"], "updated_at": DNS_CACHE["updated_at"], "error": DNS_CACHE["error"]}


def load_html():
    template = Path(__file__).resolve().parent / "templates" / "index.html"
    return template.read_text(encoding="utf-8")


def send_security_headers(handler):
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("Cache-Control", "no-store")


def json_response(handler, obj, status=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    send_security_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler, text, status=200, content_type="text/html; charset=utf-8"):
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    send_security_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def _positive_int(value, default):
    try:
        n = int(value)
    except Exception:
        return default
    return max(1, n)


def compact_log_file(path, max_bytes=None, keep_bytes=None):
    max_bytes = _positive_int(max_bytes if max_bytes is not None else LOG_MAX_BYTES, 2 * 1024 * 1024)
    keep_bytes = _positive_int(keep_bytes if keep_bytes is not None else LOG_KEEP_BYTES, 512 * 1024)
    keep_bytes = min(keep_bytes, max_bytes)
    try:
        path = Path(path)
        if not path.exists() or path.stat().st_size <= max_bytes:
            return False
        with open(path, "rb") as f:
            f.seek(-keep_bytes, os.SEEK_END)
            data = f.read()
        if b"\n" in data:
            data = data.split(b"\n", 1)[1]
        header = f"[日志自动清理] 原日志超过 {max_bytes} bytes，仅保留最近约 {keep_bytes} bytes，清理时间 {datetime.now().isoformat()}\n".encode("utf-8")
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "wb") as f:
            f.write(header)
            f.write(data)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"Log cleanup failed for {path}: {e}", flush=True)
        return False


def read_recent_log(path, tail_lines=None, tail_bytes=None):
    path = Path(path)
    if not path.exists():
        return ""
    tail_lines = _positive_int(tail_lines if tail_lines is not None else LOG_API_TAIL_LINES, 300)
    tail_bytes = _positive_int(tail_bytes if tail_bytes is not None else LOG_KEEP_BYTES, 512 * 1024)
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > tail_bytes:
                f.seek(-tail_bytes, os.SEEK_END)
            data = f.read()
        text = data.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        return "\n".join(lines[-tail_lines:]).strip()
    except Exception:
        return ""


def append_log(text):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    compact_log_file(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(text)
    compact_log_file(LOG_FILE)


def default_config():
    return {
        "cloudflare": {
            "token": "",
            "zone_id": "",
            "domain": "",
            "ttl": 60,
            "proxied": False,
            "records": [],
        },
        "advanced": dict(ADVANCED_DEFAULTS),
        "run_settings": dict(RUN_SETTINGS_DEFAULTS),
    }


def load_config():
    if not CONFIG_FILE.exists():
        return default_config()
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return default_config()


def form_state(cfg):
    cloudflare = cfg.get("cloudflare", {}) if isinstance(cfg, dict) else {}
    records = normalize_records(cfg)
    advanced = dict(ADVANCED_DEFAULTS)
    if isinstance(cfg.get("advanced"), dict):
        for k, v in cfg.get("advanced", {}).items():
            if k in advanced:
                advanced[k] = str(v)
    return {
        "token": "",
        "token_configured": bool(str(cloudflare.get("token", "") or "").strip()),
        "ttl": str(cloudflare.get("ttl", 60) or 60),
        "proxied": bool(cloudflare.get("proxied", False)),
        "records": records,
        "records_text": records_to_text(records),
        "advanced": advanced,
        "run_settings": run_settings_state(cfg),
    }


def validated_number(value, name, low, high, integer=False):
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = float(value)
        if not math.isfinite(number) or not low <= number <= high:
            raise ValueError()
        if integer and (not number.is_integer() or not re.fullmatch(r"[0-9]+", str(value))):
            raise ValueError()
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} 必须在 {low}~{high} 范围内" ) from None
    return int(number) if integer else number


def validate_advanced(advanced):
    for key, low, high in (("rounds", 1, 1000), ("download_top", 0, 10000),
                           ("switch_min_interval", 0, 31536000), ("switch_failure_threshold", 1, 20),
                           ("switch_recheck_rounds", 1, 20), ("max_scan_seconds", 0, 3600),
                           ("candidate_pool", 50, 1000), ("download_recheck_rounds", 1, 3)):
        validated_number(advanced[key], key, low, high, integer=True)
    for key in ("min_download_mbps", "max_latency_ms", "min_latency_ms", "max_loss_rate",
                "switch_min_improvement", "switch_recheck_timeout", "download_qualify_mbps"):
        high = 1 if key in ("max_loss_rate", "switch_min_improvement") else (60 if key == "switch_recheck_timeout" else 1000000)
        validated_number(advanced[key], key, 0 if key != "switch_recheck_timeout" else 0.1, high)
    if float(advanced["max_latency_ms"]) and float(advanced["min_latency_ms"]) > float(advanced["max_latency_ms"]):
        raise ValueError("最小延迟不能大于最大延迟")
    for key in ("timeout", "download_timeout", "download_window", "download_budget", "switch_download_budget"):
        value = advanced[key]
        if not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?(?:ns|us|µs|ms|s|m|h))+", value):
            raise ValueError(f"{key} 必须为正数时长，例如 6s 或 500ms")
        units = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": .001, "s": 1, "m": 60, "h": 3600}
        seconds = sum(float(n) * units[u] for n, u in re.findall(r"([0-9]+(?:\.[0-9]+)?)(ns|us|µs|ms|s|m|h)", value))
        if not 0 < seconds <= 86400:
            raise ValueError(f"{key} 必须大于 0 且不超过 24h")
        if key == "download_window" and not 0.1 <= seconds <= 30:
            raise ValueError("下载采样窗口必须在 100ms~30s 范围内")
        if key in ("download_budget", "switch_download_budget") and not 1 <= seconds <= 1800:
            raise ValueError("每族下载总预算必须在 1s~30m 范围内")
    if advanced["download_mode"] not in ("sequential", "all"):
        raise ValueError("无效下载模式")
    if advanced["select_profile"] not in ("throughput", "latency"):
        raise ValueError("无效优选目标")


def validate_run_settings(body, defaults):
    ip_mode = body.get("ip_mode", defaults["ip_mode"])
    if ip_mode not in ("4", "6", "both"):
        raise ValueError("无效 IP 模式")
    top_n = validated_number(body.get("top_n", defaults["top_n"]), "优选数量", 1, 50, integer=True)
    return {"ip_mode": ip_mode, "top_n": top_n}


def run_settings_state(cfg):
    settings = cfg.get("run_settings") if isinstance(cfg, dict) else None
    try:
        return validate_run_settings(settings if isinstance(settings, dict) else {}, RUN_SETTINGS_DEFAULTS)
    except ValueError:
        return dict(RUN_SETTINGS_DEFAULTS)


def write_config(cfg):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".config-", dir=CONFIG_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(cfg, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, CONFIG_FILE)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_run_settings(body):
    with CONFIG_LOCK:
        cfg = load_config()
        settings = validate_run_settings(body, run_settings_state(cfg))
        cfg["run_settings"] = settings
        write_config(cfg)
        return settings


def save_config_from_form(body):
    with CONFIG_LOCK:
        return _save_config_from_form(body)


def _save_config_from_form(body):
    current_cfg = load_config()
    current_token = str(current_cfg.get("cloudflare", {}).get("token", "") or "").strip()
    submitted_token = str(body.get("token", "") or "").strip()
    ttl_raw = str(body.get("ttl", "60")).strip()
    ttl = validated_number(ttl_raw, "TTL", 1, 86400, integer=True)
    records = parse_records_text(body.get("records_text", ""))
    advanced = dict(ADVANCED_DEFAULTS)
    body_advanced = body.get("advanced", {}) if isinstance(body.get("advanced", {}), dict) else {}
    for key in advanced:
        if key in body_advanced:
            advanced[key] = str(body_advanced.get(key, "")).strip()
    validate_advanced(advanced)
    advanced["keep_last_on_fail"] = "true" if str(advanced.get("keep_last_on_fail", "true")).lower() not in ("0", "false", "no", "off") else "false"
    cfg = {
        "cloudflare": {
            "token": submitted_token or current_token,
            "ttl": ttl,
            "proxied": bool(body.get("proxied", False)),
            "records": records,
        },
        "advanced": advanced,
    }
    cfg["run_settings"] = run_settings_state(current_cfg)
    if records:
        cfg["cloudflare"]["domain"] = records[0]["domain"]
        cfg["cloudflare"]["zone_id"] = records[0].get("zone_id", "")
    else:
        cfg["cloudflare"]["domain"] = ""
        cfg["cloudflare"]["zone_id"] = ""
    write_config(cfg)
    DNS_CACHE.update({"items": [], "updated_at": None, "fetched_at": None, "error": None})
    return cfg


def fill_template(template, mapping):
    for k, v in mapping.items():
        template = template.replace("{{" + k + "}}", v)
    return template


def clamp_int(value, default, low, high):
    try:
        n = int(value)
    except Exception:
        n = default
    return max(low, min(high, n))


def default_cron_text():
    # Missing schedule is disabled, matching the scheduler's actual behavior.
    return build_cron_text(False, 6, False, "both", 3)


def cron_state(text):
    state = {
        "enabled": False,
        "interval_hours": 6,
        "reboot": False,
        "ip_mode": "both",
        "top_n": 3,
        "raw": text or "",
    }
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("SHELL=") or s.startswith("PATH="):
            continue
        if s.startswith("@reboot"):
            if RUN_CMD in s:
                state["reboot"] = True
                for token in s.split():
                    if token.startswith("IP_MODE="):
                        state["ip_mode"] = token.split("=", 1)[1] or "both"
                    elif token.startswith("TOP_N="):
                        state["top_n"] = clamp_int(token.split("=", 1)[1], 3, 1, 50)
            continue
        parts = s.split(None, 5)
        if len(parts) < 6:
            continue
        minute, hour, day, month, weekday, command = parts
        if RUN_CMD not in command:
            continue
        state["enabled"] = True
        if minute == "0" and hour.startswith("*/") and day == "*" and month == "*" and weekday == "*":
            state["interval_hours"] = clamp_int(hour[2:], 6, 1, 24)
        for token in command.split():
            if token.startswith("IP_MODE="):
                state["ip_mode"] = token.split("=", 1)[1] or "both"
            elif token.startswith("TOP_N="):
                state["top_n"] = clamp_int(token.split("=", 1)[1], 3, 1, 50)
    return state


def build_cron_text(enabled, interval_hours, reboot, ip_mode, top_n):
    interval_hours = clamp_int(interval_hours, 6, 1, 24)
    top_n = clamp_int(top_n, 3, 1, 50)
    if ip_mode not in ("4", "6", "both"):
        ip_mode = "both"
    lines = [
        "SHELL=/bin/sh",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    ]
    if enabled:
        lines.append(f"0 */{interval_hours} * * * IP_MODE={ip_mode} TOP_N={top_n} {RUN_CMD} >> /data/logs/cron.log 2>&1")
    if reboot:
        lines.append(f"@reboot sleep 30 && IP_MODE={ip_mode} TOP_N={top_n} {RUN_CMD} >> /data/logs/boot.log 2>&1")
    return "\n".join(lines) + "\n"


def save_cron_text(content):
    content = (content or "").strip() + "\n"
    if not content.strip():
        raise ValueError("cron 内容不能为空")
    CRON_FILE.parent.mkdir(parents=True, exist_ok=True)
    CRON_FILE.write_text(content, encoding="utf-8")
    return content


def next_run_time(now_ts=None):
    if not CRON_FILE.exists():
        return None
    try:
        lines = CRON_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None
    now = datetime.now() if now_ts is None else datetime.fromtimestamp(now_ts)
    candidates = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("SHELL=") or s.startswith("PATH="):
            continue
        if s.startswith("@reboot"):
            continue
        parts = s.split(None, 5)
        if len(parts) < 6:
            continue
        minute, hour, day, month, weekday, command = parts
        if RUN_CMD not in command:
            continue
        for i in range(0, 24 * 60 + 1):
            cand = now + timedelta(minutes=i)
            py_weekday = (cand.weekday() + 1) % 7
            if parse_cron_field(minute, cand.minute) and parse_cron_field(hour, cand.hour) and parse_cron_field(day, cand.day) and parse_cron_field(month, cand.month) and parse_cron_field(weekday, py_weekday):
                candidates.append(cand)
                break
    if not candidates:
        return None
    return min(candidates).timestamp()


def normalize_ip_row(version, row):
    latency = row.get("latency_ms")
    if latency in (None, ""):
        latency = next((row.get(key) for key in ("total_ms", "ttfb_ms", "connect_ms")
                        if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool) and math.isfinite(row.get(key)) and row.get(key) > 0), None)
    return {
        "version": version,
        "ip": row.get("ip"),
        "latency_ms": latency,
        "connect_ms": row.get("connect_ms"),
        "tls_ms": row.get("tls_ms"),
        "ttfb_ms": row.get("ttfb_ms"),
        "total_ms": row.get("total_ms"),
        "download_mbps": row.get("download_mbps") if row.get("download_ok") is not False else None,
        "download_ok": row.get("download_ok"),
        "download_measured_at": row.get("download_measured_at"),
        "download_method": row.get("download_method"),
        "download_stop_reason": row.get("download_stop_reason"),
        "colo": row.get("colo"),
        "loss_rate": row.get("loss_rate"),
    }


def render_index():
    cfg = load_config()
    state = form_state(cfg)
    cron_text = CRON_FILE.read_text(encoding="utf-8") if CRON_FILE.exists() else default_cron_text()
    cron_form = cron_state(cron_text)
    tpl = load_html()
    mapping = {
        "CF_TOKEN": "",
        "ADV_PROFILE_THROUGHPUT": "selected" if state["advanced"].get("select_profile", "throughput") == "throughput" else "",
        "ADV_PROFILE_LATENCY": "selected" if state["advanced"].get("select_profile", "throughput") == "latency" else "",
        "ADV_CANDIDATE_POOL": html.escape(state["advanced"].get("candidate_pool", "200")),
        "ADV_DOWNLOAD_QUALIFY": html.escape(state["advanced"].get("download_qualify_mbps", "10")),
        "ADV_DOWNLOAD_RECHECK_ROUNDS": html.escape(state["advanced"].get("download_recheck_rounds", "2")),
        "ADV_SWITCH_DOWNLOAD_BUDGET": html.escape(state["advanced"].get("switch_download_budget", "180s")),
        "CF_RECORDS": html.escape(state["records_text"]),
        "CF_TTL": html.escape(state["ttl"]),
        "CF_PROXIED_CHECKED": "checked" if state["proxied"] else "",
        "RUN_TOPN": str(state["run_settings"]["top_n"]),
        "RUN_MODE_4": "selected" if state["run_settings"]["ip_mode"] == "4" else "",
        "RUN_MODE_6": "selected" if state["run_settings"]["ip_mode"] == "6" else "",
        "RUN_MODE_BOTH": "selected" if state["run_settings"]["ip_mode"] == "both" else "",
        "SCHEDULE_ENABLED_CHECKED": "checked" if cron_form["enabled"] else "",
        "SCHEDULE_REBOOT_CHECKED": "checked" if cron_form["reboot"] else "",
        "SCHEDULE_INTERVAL": str(cron_form["interval_hours"]),
        "SCHEDULE_TOPN": str(cron_form["top_n"]),
        "SCHEDULE_MODE_4": "selected" if cron_form["ip_mode"] == "4" else "",
        "SCHEDULE_MODE_6": "selected" if cron_form["ip_mode"] == "6" else "",
        "SCHEDULE_MODE_BOTH": "selected" if cron_form["ip_mode"] == "both" else "",
        "ADV_TIMEOUT": html.escape(state["advanced"].get("timeout", "6s")),
        "ADV_ROUNDS": html.escape(state["advanced"].get("rounds", "6")),
        "ADV_DOWNLOAD_TOP": html.escape(state["advanced"].get("download_top", "8")),
        "ADV_DOWNLOAD_TIMEOUT": html.escape(state["advanced"].get("download_timeout", "15s")),
        "ADV_DOWNLOAD_WINDOW": html.escape(state["advanced"].get("download_window", "6s")),
        "ADV_DOWNLOAD_BUDGET": html.escape(state["advanced"].get("download_budget", "180s")),
        "ADV_MAX_SCAN_SECONDS": html.escape(state["advanced"].get("max_scan_seconds", "300")),
        "ADV_DOWNLOAD_MODE_SEQ": "selected" if state["advanced"].get("download_mode", "sequential") == "sequential" else "",
        "ADV_DOWNLOAD_MODE_ALL": "selected" if state["advanced"].get("download_mode", "sequential") == "all" else "",
        "ADV_DOWNLOAD_URL": html.escape(state["advanced"].get("download_url", "")),
        "ADV_MIN_DOWNLOAD_MBPS": html.escape(state["advanced"].get("min_download_mbps", "0")),
        "ADV_MAX_LATENCY_MS": html.escape(state["advanced"].get("max_latency_ms", "0")),
        "ADV_MIN_LATENCY_MS": html.escape(state["advanced"].get("min_latency_ms", "0")),
        "ADV_MAX_LOSS_RATE": html.escape(state["advanced"].get("max_loss_rate", "1")),
        "ADV_CF_COLO": html.escape(state["advanced"].get("cf_colo", "")),
        "ADV_KEEP_LAST_CHECKED": "checked" if state["advanced"].get("keep_last_on_fail", "true").lower() not in ("0", "false", "no", "off") else "",
        "ADV_SWITCH_MIN_IMPROVEMENT": html.escape(state["advanced"].get("switch_min_improvement", "0.20")),
        "ADV_SWITCH_MIN_INTERVAL": html.escape(state["advanced"].get("switch_min_interval", "21600")),
        "ADV_SWITCH_FAILURE_THRESHOLD": html.escape(state["advanced"].get("switch_failure_threshold", "2")),
        "ADV_SWITCH_RECHECK_ROUNDS": html.escape(state["advanced"].get("switch_recheck_rounds", "3")),
        "ADV_SWITCH_RECHECK_TIMEOUT": html.escape(state["advanced"].get("switch_recheck_timeout", "5")),
    }
    return fill_template(tpl, mapping)


def parse_cron_field(field, value):
    field = field.strip()
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return value % step == 0
    ok = False
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if int(a) <= value <= int(b):
                ok = True
        elif part.isdigit() and int(part) == value:
            ok = True
    return ok


def maybe_run_scheduled(now):
    if not CRON_FILE.exists():
        return
    lines = CRON_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("SHELL=") or s.startswith("PATH="):
            continue
        if s.startswith("@reboot"):
            continue
        parts = s.split(None, 5)
        if len(parts) < 6:
            continue
        minute, hour, day, month, weekday, command = parts
        py_weekday = (now.weekday() + 1) % 7
        if not (parse_cron_field(minute, now.minute) and parse_cron_field(hour, now.hour) and parse_cron_field(day, now.day) and parse_cron_field(month, now.month) and parse_cron_field(weekday, py_weekday)):
            continue
        if RUN_CMD not in command:
            continue
        env = os.environ.copy()
        for token in command.split():
            if "=" in token and not token.startswith("/"):
                k, v = token.split("=", 1)
                env[k] = v
        trigger_run(env)
        break


def schedule_loop():
    last_minute = None
    while True:
        try:
            now = datetime.now()
            key = now.strftime("%Y-%m-%d %H:%M")
            if key != last_minute:
                last_minute = key
                maybe_run_scheduled(now)
        except Exception as e:
            append_log(f"[计划任务错误] {e}\n")
        time.sleep(1)


def apply_advanced_env(env):
    cfg = load_config()
    advanced = cfg.get("advanced", {}) if isinstance(cfg, dict) else {}
    mapping = {
        "timeout": "TIMEOUT",
        "select_profile": "SELECT_PROFILE",
        "candidate_pool": "CANDIDATE_POOL",
        "download_qualify_mbps": "DOWNLOAD_QUALIFY_MBPS",
        "download_recheck_rounds": "DOWNLOAD_RECHECK_ROUNDS",
        "switch_download_budget": "SWITCH_DOWNLOAD_BUDGET",
        "rounds": "ROUNDS",
        "download_top": "DOWNLOAD_TOP",
        "download_timeout": "DOWNLOAD_TIMEOUT",
        "download_window": "DOWNLOAD_WINDOW",
        "download_budget": "DOWNLOAD_BUDGET",
        "max_scan_seconds": "MAX_SCAN_SECONDS",
        "download_mode": "DOWNLOAD_MODE",
        "download_url": "DOWNLOAD_URL",
        "min_download_mbps": "MIN_DOWNLOAD_MBPS",
        "max_latency_ms": "MAX_LATENCY_MS",
        "min_latency_ms": "MIN_LATENCY_MS",
        "max_loss_rate": "MAX_LOSS_RATE",
        "cf_colo": "CF_COLO",
        "keep_last_on_fail": "KEEP_LAST_ON_FAIL",
        "switch_min_improvement": "SWITCH_MIN_IMPROVEMENT",
        "switch_min_interval": "SWITCH_MIN_INTERVAL",
        "switch_failure_threshold": "SWITCH_FAILURE_THRESHOLD",
        "switch_recheck_rounds": "SWITCH_RECHECK_ROUNDS",
        "switch_recheck_timeout": "SWITCH_RECHECK_TIMEOUT",
    }
    for key, env_key in mapping.items():
        val = str(advanced.get(key, "") or "").strip()
        if val:
            env[env_key] = val
    return env


def trigger_run(env=None):
    global RUN_THREAD
    with RUN_LOCK:
        if LAST_RUN["running"]:
            return False
        env = apply_advanced_env(env if env is not None else os.environ.copy())
        LAST_RUN.update({"running": True, "cancelled": False, "code": None,
                         "stdout": "", "stderr": "", "started_at": time.time(),
                         "ended_at": None, "selected_ips": []})
        RUN_THREAD = threading.Thread(target=run_job, args=(env,), daemon=True)
        try:
            RUN_THREAD.start()
        except Exception:
            LAST_RUN["running"] = False
            raise
        return True


def stop_run():
    # Kill the entire session immediately: graceful SIGTERM can let the Python
    # runner proceed to DNS writes after its mcis child exits. Do not use it.
    with RUN_LOCK:
        if not LAST_RUN["running"]:
            return False
        LAST_RUN["cancelled"] = True
        if RUN_PROCESS is not None:
            try:
                os.killpg(RUN_PROCESS.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return True


def run_job(env):
    global RUN_PROCESS
    proc = None
    code = None
    error = ""
    memory_limit = _positive_int(LOG_MEMORY_MAX_BYTES, 16000)
    chunks = deque()
    chunks_size = 0
    try:
        compact_log_file(LOG_FILE)
        compact_log_file(INIT_LOG_FILE)
        compact_log_file(BOOT_LOG_FILE)
        append_log(f"\n===== {datetime.now().isoformat()} 开始执行 =====\n")
        with RUN_LOCK:
            if LAST_RUN["cancelled"]:
                return
            proc = subprocess.Popen(
                [RUN_CMD], text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, env=env, bufsize=1,
                start_new_session=True,
            )
            RUN_PROCESS = proc
        if proc.stdout is not None:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                chunks.append(line)
                chunks_size += len(line.encode("utf-8", errors="ignore"))
                while chunks_size > memory_limit and chunks:
                    removed = chunks.popleft()
                    chunks_size -= len(removed.encode("utf-8", errors="ignore"))
                LAST_RUN["stdout"] = "".join(chunks)
                append_log(line)
        code = proc.wait()
    except Exception as exc:
        error = str(exc)
        code = -1
    finally:
        if proc is not None:
            # Also reap descendants if an unexpected logging/read error occurs.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()
        with RUN_LOCK:
            RUN_PROCESS = None
            DNS_CACHE.update({"items": [], "updated_at": None, "fetched_at": None})
            LAST_RUN.update({"running": False, "code": -signal.SIGKILL if LAST_RUN["cancelled"] else code,
                             "stdout": "".join(chunks), "stderr": error,
                             "ended_at": time.time()})
        try:
            append_log(f"===== {datetime.now().isoformat()} 执行结束，退出码={LAST_RUN['code']} =====\n")
        except OSError:
            pass


def load_run_history():
    try:
        path = PROJECT_DIR / "run_history.json"
        if path.stat().st_size > 2 * 1024 * 1024:
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return data[-30:] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def load_scan_only_results():
    items = []
    for version in ("4", "6"):
        try:
            data = json.loads((PROJECT_DIR / f"scan_only_v{version}.json").read_text(encoding="utf-8"))
            items.extend(normalize_ip_row(version, row) for row in data["result"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return items


def export_results(fmt, version):
    if fmt not in ("csv", "txt") or version not in ("4", "6", "both"):
        raise ValueError("无效导出格式或 IP 模式")
    rows = []
    seen = set()
    for item in get_selected_ips()["items"]:
        try:
            ip = ipaddress.ip_address(item.get("ip", ""))
        except ValueError:
            continue
        if (version != "both" and str(ip.version) != version) or str(ip) in seen:
            continue
        seen.add(str(ip))
        row = {"version": str(ip.version), "ip": str(ip)}
        # Only allow numeric metrics: no arbitrary strings/spreadsheet formulas.
        for key in ("latency_ms", "connect_ms", "tls_ms", "ttfb_ms", "total_ms", "download_mbps", "loss_rate"):
            try:
                number = float(item.get(key))
                row[key] = str(number) if math.isfinite(number) and number >= 0 else ""
            except (TypeError, ValueError):
                row[key] = ""
        colo = str(item.get("colo", "") or "").strip().upper()
        row["colo"] = colo[:64] if re.fullmatch(r"[A-Z0-9_-]{1,64}", colo) else ""
        rows.append(row)
    if fmt == "txt":
        return "".join(row["ip"] + "\n" for row in rows)
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=("version", "ip", "colo", "latency_ms", "connect_ms", "tls_ms", "ttfb_ms", "total_ms", "download_mbps", "loss_rate"))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


class Handler(BaseHTTPRequestHandler):
    def authenticated(self):
        username = os.getenv("WEB_USERNAME", "")
        password = os.getenv("WEB_PASSWORD", "")
        if not username and not password:
            return True
        supplied = ""
        try:
            scheme, encoded = self.headers.get("Authorization", "").split(" ", 1)
            if scheme.lower() == "basic":
                supplied = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeError):
            pass
        # Partial configuration fails closed, never silently disables auth.
        if username and password and hmac.compare_digest(supplied.encode(), f"{username}:{password}".encode()):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="cfipup2dns", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        send_security_headers(self)
        self.end_headers()
        self.close_connection = True
        return False

    def mutation_allowed(self):
        if self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
            return False
        origin = self.headers.get("Origin")
        if origin is not None:
            try:
                parsed = urlparse(origin)
            except ValueError:
                return False
            if (parsed.scheme not in ("http", "https") or parsed.netloc.lower() != self.headers.get("Host", "").lower()
                    or parsed.path or parsed.query or parsed.fragment or parsed.username):
                return False
        return True

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return json_response(self, {"ok": True, "service": "cfipup2dns"})
        if not self.authenticated():
            return
        if path == "/api/export":
            query = urllib.parse.parse_qs(urlparse(self.path).query)
            fmt = query.get("format", ["csv"])[0]
            version = query.get("ip_mode", ["both"])[0]
            try:
                body = export_results(fmt, version).encode("utf-8")
            except ValueError as exc:
                return json_response(self, {"ok": False, "error": str(exc)}, 400)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8" if fmt == "csv" else "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="best-ips-{version}.{fmt}"')
            self.send_header("Content-Length", str(len(body)))
            send_security_headers(self)
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/":
            return text_response(self, render_index())
        if path == "/api/logs":
            live_parts = []
            if LAST_RUN.get("running"):
                live_parts.append("[运行中] 实时刷新中...")
            if LAST_RUN.get("started_at"):
                live_parts.append(f"开始时间: {datetime.fromtimestamp(LAST_RUN['started_at']).isoformat()}")
            if LAST_RUN.get("ended_at"):
                live_parts.append(f"结束时间: {datetime.fromtimestamp(LAST_RUN['ended_at']).isoformat()}")
            if LAST_RUN.get("code") is not None:
                live_parts.append(f"退出码: {LAST_RUN['code']}")
            live_logs = "\n\n".join([p for p in live_parts if p]).strip()
            compact_log_file(LOG_FILE)
            compact_log_file(INIT_LOG_FILE)
            compact_log_file(BOOT_LOG_FILE)
            file_logs = read_recent_log(LOG_FILE, LOG_API_TAIL_LINES, LOG_KEEP_BYTES)
            init_logs = read_recent_log(INIT_LOG_FILE, 200, LOG_KEEP_BYTES)
            boot_logs = read_recent_log(BOOT_LOG_FILE, 200, LOG_KEEP_BYTES)
            combined = "\n\n".join([p for p in [live_logs, init_logs, boot_logs, file_logs] if p]).strip()
            return json_response(self, {"ok": True, "logs": combined or "(暂无日志)"})
        if path == "/api/history":
            return json_response(self, {"ok": True, "history": load_run_history()})
        if path == "/api/scan-results":
            return json_response(self, {"ok": True, "result": load_scan_only_results()})
        if path == "/api/status":
            selected = get_selected_ips()
            with RUN_LOCK:
                payload = {"ok": True, **LAST_RUN}
            history = load_run_history()
            payload["last_summary"] = history[-1] if history else None
            payload["next_run_at"] = next_run_time()
            payload["selected_ips"] = selected["items"]
            payload["selected_ips_updated_at"] = selected["updated_at"]
            payload["dns_error"] = selected.get("error")
            return json_response(self, payload)
        if path == "/api/run-settings":
            return json_response(self, {"ok": True, "run_settings": run_settings_state(load_config())})
        if path == "/api/config":
            cfg = load_config()
            form = form_state(cfg)
            return json_response(self, {"ok": True, "form": form})
        if path == "/api/schedule":
            raw = CRON_FILE.read_text(encoding="utf-8") if CRON_FILE.exists() else default_cron_text()
            return json_response(self, {"ok": True, "schedule": cron_state(raw)})
        if path == "/api/best-ips":
            selected = get_selected_ips()
            return json_response(self, {
                "ok": True,
                "result": selected["items"],
                "updated_at": selected["updated_at"],
                "next_run_at": next_run_time(),
                "dns_error": selected.get("error"),
            })
        return text_response(self, "Not Found", 404, "text/plain; charset=utf-8")

    def do_POST(self):
        path = urlparse(self.path).path
        if not self.authenticated():
            return
        if not self.mutation_allowed():
            self.close_connection = True
            return json_response(self, {"ok": False, "error": "跨站请求被拒绝"}, 403)
        try:
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) > 1:
                raise ValueError()
            length_text = self.headers.get("Content-Length", "0")
            if not re.fullmatch(r"[0-9]+", length_text):
                raise ValueError()
            length = int(length_text)
        except ValueError:
            self.close_connection = True
            return json_response(self, {"ok": False, "error": "无效请求长度"}, 400)
        if length > MAX_REQUEST_BYTES:
            self.close_connection = True
            return json_response(self, {"ok": False, "error": "请求过大"}, 413)
        if length and self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self.close_connection = True
            return json_response(self, {"ok": False, "error": "请使用 application/json"}, 415)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError()
            if path == "/api/config":
                if "advanced" in body and not isinstance(body["advanced"], dict):
                    raise ValueError("advanced 必须是对象")
            if path == "/api/run" and not isinstance(body.get("scan_only", False), bool):
                raise ValueError("scan_only 必须为布尔值")
            if path in ("/api/run", "/api/run-settings"):
                validate_run_settings(body, run_settings_state(load_config()))
            if path == "/api/schedule":
                if body.get("ip_mode", "both") not in ("4", "6", "both"):
                    raise ValueError("无效 IP 模式")
                validated_number(body.get("top_n", 3), "优选数量", 1, 50, integer=True)
                validated_number(body.get("interval_hours", 6), "计划间隔", 1, 24, integer=True)
        except (ValueError, UnicodeError) as exc:
            return json_response(self, {"ok": False, "error": str(exc) or "无效 JSON 对象"}, 400)

        if path == "/api/config":
            try:
                cfg = save_config_from_form(body)
            except ValueError as exc:
                return json_response(self, {"ok": False, "error": str(exc)}, 400)
            form = form_state(cfg)
            return json_response(self, {"ok": True, "form": form})

        if path == "/api/schedule":
            content = build_cron_text(
                bool(body.get("enabled", False)),
                body.get("interval_hours", 6),
                bool(body.get("reboot", False)),
                body.get("ip_mode", "both"),
                body.get("top_n", 3),
            )
            save_cron_text(content)
            return json_response(self, {"ok": True, "schedule": cron_state(content)})

        if path == "/api/stop":
            stopped = stop_run()
            return json_response(self, {"ok": stopped, "running": LAST_RUN["running"],
                                        "cancelled": LAST_RUN["cancelled"]}, 200 if stopped else 409)

        if path == "/api/run-settings":
            settings = save_run_settings(body)
            return json_response(self, {"ok": True, "run_settings": settings})

        if path == "/api/run":
            with RUN_LOCK:
                if LAST_RUN["running"]:
                    return json_response(self, {"ok": False, "running": True}, 409)
                settings = save_run_settings(body)
                env = os.environ.copy()
                env["IP_MODE"] = settings["ip_mode"]
                env["TOP_N"] = str(settings["top_n"])
                env["SCAN_ONLY"] = "true" if body.get("scan_only", False) else "false"
                ok = trigger_run(env)
                return json_response(self, {"ok": ok, "running": LAST_RUN["running"],
                                            "run_settings": settings}, 200 if ok else 409)

        if path == "/api/logs/clear":
            if LOG_FILE.exists():
                LOG_FILE.write_text("", encoding="utf-8")
            if INIT_LOG_FILE.exists():
                INIT_LOG_FILE.write_text("", encoding="utf-8")
            LAST_RUN["stdout"] = ""
            LAST_RUN["stderr"] = ""
            return json_response(self, {"ok": True})

        return json_response(self, {"ok": False, "error": "not found"}, 404)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=schedule_loop, daemon=True).start()
    server = ThreadingHTTPServer((WEB_BIND_HOST, PORT), Handler)
    server.serve_forever()
