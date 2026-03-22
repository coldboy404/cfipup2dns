#!/usr/bin/env python3
import html
import json
import os
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PROJECT_DIR = Path(os.getenv("PROJECT_DIR", "/data/project"))
CONFIG_FILE = Path(os.getenv("CONFIG_FILE", str(PROJECT_DIR / "config.json")))
CRON_FILE = Path(os.getenv("CRON_FILE", "/data/cron/cfip.cron"))
LOG_FILE = Path(os.getenv("LOG_FILE", "/data/logs/cron.log"))
PORT = int(os.getenv("PORT", "9527"))
RUN_CMD = "/opt/cfipup2dns/cfip.sh"
WEEKDAY_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]

LAST_RUN = {"running": False, "code": None, "stdout": "", "stderr": "", "started_at": None, "ended_at": None}


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
    return {
        "token": str(cloudflare.get("token", "") or ""),
        "zone_id": str(cloudflare.get("zone_id", "") or ""),
        "domain": str(cloudflare.get("domain", "") or ""),
        "ttl": str(cloudflare.get("ttl", 60) or 60),
        "proxied": bool(cloudflare.get("proxied", False)),
    }


def save_config_from_form(body):
    ttl_raw = str(body.get("ttl", "60") or "60").strip()
    try:
        ttl = int(ttl_raw)
    except Exception:
        ttl = 60
    ttl = max(1, min(ttl, 86400))
    cfg = {
        "cloudflare": {
            "token": str(body.get("token", "") or "").strip(),
            "zone_id": str(body.get("zone_id", "") or "").strip(),
            "domain": str(body.get("domain", "") or "").strip(),
            "ttl": ttl,
            "proxied": bool(body.get("proxied", False)),
        }
    }
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def fill_template(template, mapping):
    for k, v in mapping.items():
        template = template.replace("{{" + k + "}}", v)
    return template


def default_cron_text():
    return build_cron_text(True, "02:00", list(range(7)), True, "both", 5)


def parse_time_hhmm(value):
    s = str(value or "02:00").strip()
    if ":" not in s:
        return "02:00"
    hh, mm = s.split(":", 1)
    try:
        hh = max(0, min(23, int(hh)))
        mm = max(0, min(59, int(mm)))
    except Exception:
        return "02:00"
    return f"{hh:02d}:{mm:02d}"


def normalize_weekdays(values):
    if values in (None, "", []):
        return list(range(7))
    if isinstance(values, str):
        values = [v.strip() for v in values.split(",") if v.strip() != ""]
    out = []
    for v in values:
        try:
            iv = int(v)
        except Exception:
            continue
        if 0 <= iv <= 6 and iv not in out:
            out.append(iv)
    return sorted(out) or list(range(7))


def cron_state(text):
    state = {
        "enabled": False,
        "time": "02:00",
        "weekdays": list(range(7)),
        "reboot": False,
        "ip_mode": "both",
        "top_n": 5,
        "raw": text or "",
    }
    lines = (text or "").splitlines()
    for line in lines:
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
                        try:
                            state["top_n"] = max(1, int(token.split("=", 1)[1]))
                        except Exception:
                            pass
            continue
        parts = s.split(None, 5)
        if len(parts) < 6:
            continue
        minute, hour, _day, _month, weekday, command = parts
        if RUN_CMD not in command:
            continue
        state["enabled"] = True
        try:
            hh = max(0, min(23, int(hour)))
            mm = max(0, min(59, int(minute)))
            state["time"] = f"{hh:02d}:{mm:02d}"
        except Exception:
            if minute == "0" and hour.startswith("*/"):
                state["time"] = "00:00"
        if weekday.strip() == "*":
            state["weekdays"] = list(range(7))
        else:
            days = []
            for p in weekday.split(","):
                p = p.strip()
                if p.isdigit():
                    iv = int(p)
                    if 0 <= iv <= 6 and iv not in days:
                        days.append(iv)
            state["weekdays"] = sorted(days) or list(range(7))
        for token in command.split():
            if token.startswith("IP_MODE="):
                state["ip_mode"] = token.split("=", 1)[1] or "both"
            elif token.startswith("TOP_N="):
                try:
                    state["top_n"] = max(1, int(token.split("=", 1)[1]))
                except Exception:
                    pass
        continue
    return state


def build_cron_text(enabled, run_time, weekdays, reboot, ip_mode, top_n):
    run_time = parse_time_hhmm(run_time)
    weekdays = normalize_weekdays(weekdays)
    try:
        top_n = int(top_n)
    except Exception:
        top_n = 5
    top_n = max(1, min(top_n, 50))
    if ip_mode not in ("4", "6", "both"):
        ip_mode = "both"
    hh, mm = run_time.split(":", 1)
    weekday_field = "*" if weekdays == list(range(7)) else ",".join(str(v) for v in weekdays)

    lines = [
        "SHELL=/bin/sh",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    ]
    if enabled:
        lines.append(f"{int(mm)} {int(hh)} * * {weekday_field} IP_MODE={ip_mode} TOP_N={top_n} {RUN_CMD} >> /data/logs/cron.log 2>&1")
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


def render_index():
    cfg = load_config()
    state = form_state(cfg)
    cron_text = CRON_FILE.read_text(encoding="utf-8") if CRON_FILE.exists() else default_cron_text()
    cron_form = cron_state(cron_text)
    tpl = load_html()
    mapping = {
        "CF_TOKEN": html.escape(state["token"]),
        "CF_ZONE_ID": html.escape(state["zone_id"]),
        "CF_DOMAIN": html.escape(state["domain"]),
        "CF_TTL": html.escape(state["ttl"]),
        "CF_PROXIED_CHECKED": "checked" if state["proxied"] else "",
        "CRON_TEXT": html.escape(cron_text),
        "SCHEDULE_ENABLED_CHECKED": "checked" if cron_form["enabled"] else "",
        "SCHEDULE_REBOOT_CHECKED": "checked" if cron_form["reboot"] else "",
        "SCHEDULE_TIME": html.escape(cron_form["time"]),
        "SCHEDULE_TOPN": str(cron_form["top_n"]),
        "SCHEDULE_MODE_4": "selected" if cron_form["ip_mode"] == "4" else "",
        "SCHEDULE_MODE_6": "selected" if cron_form["ip_mode"] == "6" else "",
        "SCHEDULE_MODE_BOTH": "selected" if cron_form["ip_mode"] == "both" else "",
    }
    for i, name in enumerate(WEEKDAY_NAMES):
        mapping[f"WD_{name.upper()}_CHECKED"] = "checked" if i in cron_form["weekdays"] else ""
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
            append_log(f"[scheduler-error] {e}\n")
        time.sleep(1)


def trigger_run(env=None):
    if LAST_RUN["running"]:
        return False
    t = threading.Thread(target=run_job, args=(env or os.environ.copy(),), daemon=True)
    t.start()
    return True


def run_job(env):
    LAST_RUN.update({"running": True, "code": None, "stdout": "", "stderr": "", "started_at": time.time(), "ended_at": None})
    p = subprocess.run(RUN_CMD, shell=True, text=True, capture_output=True, env=env)
    LAST_RUN.update({"running": False, "code": p.returncode, "stdout": p.stdout[-8000:], "stderr": p.stderr[-8000:], "ended_at": time.time()})
    append_log(f"\n===== {datetime.now().isoformat()} code={p.returncode} =====\n{p.stdout}\n{p.stderr}\n")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return text_response(self, render_index())
        if path == "/api/logs":
            live_parts = []
            if LAST_RUN.get("running"):
                live_parts.append("[运行中]")
            if LAST_RUN.get("started_at"):
                live_parts.append(f"started_at={datetime.fromtimestamp(LAST_RUN['started_at']).isoformat()}")
            if LAST_RUN.get("ended_at"):
                live_parts.append(f"ended_at={datetime.fromtimestamp(LAST_RUN['ended_at']).isoformat()}")
            if LAST_RUN.get("code") is not None:
                live_parts.append(f"exit_code={LAST_RUN['code']}")
            if LAST_RUN.get("stdout"):
                live_parts.append("[stdout]\n" + LAST_RUN["stdout"])
            if LAST_RUN.get("stderr"):
                live_parts.append("[stderr]\n" + LAST_RUN["stderr"])
            live_logs = "\n\n".join([p for p in live_parts if p]).strip()
            file_logs = ""
            if LOG_FILE.exists():
                file_logs = "\n".join(LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]).strip()
            combined = "\n\n".join([p for p in [live_logs, file_logs] if p]).strip()
            return json_response(self, {"ok": True, "logs": combined or "(暂无日志)"})
        if path == "/api/status":
            return json_response(self, {"ok": True, **LAST_RUN})
        if path == "/api/config":
            cfg = load_config()
            return json_response(self, {"ok": True, "config": cfg, "form": form_state(cfg)})
        if path == "/api/schedule":
            raw = CRON_FILE.read_text(encoding="utf-8") if CRON_FILE.exists() else default_cron_text()
            return json_response(self, {"ok": True, "schedule": cron_state(raw), "raw": raw})
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

        if path == "/api/cron":
            try:
                content = save_cron_text(body.get("content") or "")
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            return json_response(self, {"ok": True, "raw": content, "schedule": cron_state(content)})

        if path == "/api/schedule":
            content = build_cron_text(
                bool(body.get("enabled", False)),
                body.get("time", "02:00"),
                body.get("weekdays", list(range(7))),
                bool(body.get("reboot", False)),
                body.get("ip_mode", "both"),
                body.get("top_n", 5),
            )
            save_cron_text(content)
            return json_response(self, {"ok": True, "raw": content, "schedule": cron_state(content)})

        if path == "/api/run":
            env = os.environ.copy()
            env["IP_MODE"] = str(body.get("ip_mode", "both"))
            env["TOP_N"] = str(body.get("top_n", 5))
            ok = trigger_run(env)
            return json_response(self, {"ok": ok, "running": LAST_RUN["running"]})

        return json_response(self, {"ok": False, "error": "not found"}, 404)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=schedule_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
