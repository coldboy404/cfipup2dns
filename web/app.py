#!/usr/bin/env python3
import json
import os
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_DIR = Path(os.getenv("PROJECT_DIR", "/data/project"))
CONFIG_FILE = Path(os.getenv("CONFIG_FILE", str(PROJECT_DIR / "config.json")))
CRON_FILE = Path(os.getenv("CRON_FILE", "/data/cron/cfip.cron"))
LOG_FILE = Path(os.getenv("LOG_FILE", "/data/logs/cron.log"))
PORT = int(os.getenv("PORT", "9527"))
RUN_CMD = "/opt/cfipup2dns/cfip.sh"

LAST_RUN = {"running": False, "code": None, "stdout": "", "stderr": "", "started_at": None, "ended_at": None}


def load_html():
    p = Path("/opt/cfipup2dns/web/templates/index.html")
    return p.read_text(encoding="utf-8")


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


def append_log(text):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(text)


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
            cfg = {}
            if CONFIG_FILE.exists():
                cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cron_text = CRON_FILE.read_text(encoding="utf-8") if CRON_FILE.exists() else ""
            html = load_html().replace("{{CFG_JSON}}", json.dumps(cfg, ensure_ascii=False, indent=2)).replace("{{CRON_TEXT}}", cron_text)
            return text_response(self, html)
        if path == "/api/logs":
            if not LOG_FILE.exists():
                return json_response(self, {"ok": True, "logs": "(暂无日志)"})
            logs = "\n".join(LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:])
            return json_response(self, {"ok": True, "logs": logs})
        if path == "/api/status":
            return json_response(self, {"ok": True, **LAST_RUN})
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
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(body or {}, ensure_ascii=False, indent=2), encoding="utf-8")
            return json_response(self, {"ok": True})

        if path == "/api/cron":
            content = (body.get("content") or "").strip() + "\n"
            if not content.strip():
                return json_response(self, {"ok": False, "error": "cron 内容不能为空"}, 400)
            CRON_FILE.parent.mkdir(parents=True, exist_ok=True)
            CRON_FILE.write_text(content, encoding="utf-8")
            return json_response(self, {"ok": True})

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
