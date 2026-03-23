#!/usr/bin/env python3
import html
import json
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PROJECT_DIR = Path(os.getenv("PROJECT_DIR", "/data/project"))
CONFIG_FILE = Path(os.getenv("CONFIG_FILE", str(PROJECT_DIR / "config.json")))
CRON_FILE = Path(os.getenv("CRON_FILE", "/data/cron/cfip.cron"))
LOG_FILE = Path(os.getenv("LOG_FILE", "/data/logs/cron.log"))
INIT_LOG_FILE = Path("/data/logs/init-mcis.log")
PORT = int(os.getenv("PORT", "9527"))
RUN_CMD = "/opt/cfipup2dns/cfip.sh"
BEST_IP_FILES = {
    "4": PROJECT_DIR / "best_ips_v4.json",
    "6": PROJECT_DIR / "best_ips_v6.json",
}

LAST_RUN = {
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
        for row in obj.get("result", []) or []:
            ip = row.get("ip")
            if not ip:
                continue
            items.append(normalize_ip_row(version, row))
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
    return Path("/opt/cfipup2dns/web/templates/index.html").read_text(encoding="utf-8")


def json_response(handler, obj, status=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler, text, status=200, content_type="text/html; charset=utf-8"):
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def append_log(text):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(text)


def default_config():
    return {
        "cloudflare": {
            "token": "",
            "zone_id": "",
            "domain": "",
            "ttl": 60,
            "proxied": False,
            "records": [],
        }
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
    return {
        "token": str(cloudflare.get("token", "") or ""),
        "ttl": str(cloudflare.get("ttl", 60) or 60),
        "proxied": bool(cloudflare.get("proxied", False)),
        "records": records,
        "records_text": records_to_text(records),
    }


def save_config_from_form(body):
    ttl_raw = str(body.get("ttl", "60") or "60").strip()
    try:
        ttl = int(ttl_raw)
    except Exception:
        ttl = 60
    ttl = max(1, min(ttl, 86400))
    records = parse_records_text(body.get("records_text", ""))
    cfg = {
        "cloudflare": {
            "token": str(body.get("token", "") or "").strip(),
            "ttl": ttl,
            "proxied": bool(body.get("proxied", False)),
            "records": records,
        }
    }
    if records:
        cfg["cloudflare"]["domain"] = records[0]["domain"]
        cfg["cloudflare"]["zone_id"] = records[0].get("zone_id", "")
    else:
        cfg["cloudflare"]["domain"] = ""
        cfg["cloudflare"]["zone_id"] = ""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
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
    return build_cron_text(True, 6, True, "both", 3)


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
        latency = row.get("total_ms") or row.get("ttfb_ms") or row.get("connect_ms")
    return {
        "version": version,
        "ip": row.get("ip"),
        "latency_ms": latency,
        "download_mbps": row.get("download_mbps"),
    }


def render_index():
    cfg = load_config()
    state = form_state(cfg)
    cron_text = CRON_FILE.read_text(encoding="utf-8") if CRON_FILE.exists() else default_cron_text()
    cron_form = cron_state(cron_text)
    tpl = load_html()
    mapping = {
        "CF_TOKEN": html.escape(state["token"]),
        "CF_RECORDS": html.escape(state["records_text"]),
        "CF_TTL": html.escape(state["ttl"]),
        "CF_PROXIED_CHECKED": "checked" if state["proxied"] else "",
        "SCHEDULE_ENABLED_CHECKED": "checked" if cron_form["enabled"] else "",
        "SCHEDULE_REBOOT_CHECKED": "checked" if cron_form["reboot"] else "",
        "SCHEDULE_INTERVAL": str(cron_form["interval_hours"]),
        "SCHEDULE_TOPN": str(cron_form["top_n"]),
        "SCHEDULE_MODE_4": "selected" if cron_form["ip_mode"] == "4" else "",
        "SCHEDULE_MODE_6": "selected" if cron_form["ip_mode"] == "6" else "",
        "SCHEDULE_MODE_BOTH": "selected" if cron_form["ip_mode"] == "both" else "",
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


def trigger_run(env=None):
    if LAST_RUN["running"]:
        return False
    t = threading.Thread(target=run_job, args=(env or os.environ.copy(),), daemon=True)
    t.start()
    return True


def run_job(env):
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1024 * 1024:
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-500:])
    except Exception as e:
        print(f"Log cleanup failed: {e}", flush=True)

    LAST_RUN.update({
        "running": True,
        "code": None,
        "stdout": "",
        "stderr": "",
        "started_at": time.time(),
        "ended_at": None,
        "selected_ips": [],
    })
    chunks = []
    append_log(f"\n===== {datetime.now().isoformat()} 开始执行 =====\n")
    proc = subprocess.Popen(
        RUN_CMD,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        bufsize=1,
    )
    try:
        if proc.stdout is not None:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                chunks.append(line)
                current = "".join(chunks)[-16000:]
                LAST_RUN["stdout"] = current
                append_log(line)
        code = proc.wait()
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
    DNS_CACHE.update({"items": [], "updated_at": None, "fetched_at": None})
    LAST_RUN.update({
        "running": False,
        "code": code,
        "stdout": "".join(chunks)[-16000:],
        "stderr": "",
        "ended_at": time.time(),
        "selected_ips": get_selected_ips()["items"],
    })
    append_log(f"===== {datetime.now().isoformat()} 执行结束，退出码={code} =====\n")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
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
            file_logs = ""
            if LOG_FILE.exists():
                file_logs = "\n".join(LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]).strip()
            init_logs = ""
            if INIT_LOG_FILE.exists():
                init_logs = "\n".join(INIT_LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]).strip()
            combined = "\n\n".join([p for p in [live_logs, init_logs, file_logs] if p]).strip()
            return json_response(self, {"ok": True, "logs": combined or "(暂无日志)"})
        if path == "/api/status":
            selected = get_selected_ips()
            payload = {"ok": True, **LAST_RUN, "next_run_at": next_run_time()}
            payload["selected_ips"] = selected["items"]
            payload["selected_ips_updated_at"] = selected["updated_at"]
            payload["dns_error"] = selected.get("error")
            return json_response(self, payload)
        if path == "/api/config":
            cfg = load_config()
            return json_response(self, {"ok": True, "config": cfg, "form": form_state(cfg)})
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
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}

        if path == "/api/config":
            cfg = save_config_from_form(body or {})
            return json_response(self, {"ok": True, "config": cfg, "form": form_state(cfg)})

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

        if path == "/api/run":
            env = os.environ.copy()
            env["IP_MODE"] = str(body.get("ip_mode", "both"))
            env["TOP_N"] = str(body.get("top_n", 5))
            ok = trigger_run(env)
            return json_response(self, {"ok": ok, "running": LAST_RUN["running"]})

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
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
