#!/usr/bin/env python3
import json
import os
import subprocess
from pathlib import Path
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

PROJECT_DIR = Path(os.getenv("PROJECT_DIR", "/data/project"))
CONFIG_FILE = Path(os.getenv("CONFIG_FILE", str(PROJECT_DIR / "config.json")))
CRON_FILE = Path("/data/cron/cfip.cron")
LOG_FILE = Path("/data/logs/cron.log")


def run(cmd, env=None):
    p = subprocess.run(cmd, shell=True, text=True, capture_output=True, env=env)
    return p.returncode, p.stdout, p.stderr


@app.route("/")
def index():
    cfg = {}
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    cron_text = CRON_FILE.read_text(encoding="utf-8") if CRON_FILE.exists() else ""
    return render_template("index.html", cfg=cfg, cron_text=cron_text)


@app.post("/api/config")
def save_config():
    data = request.json or {}
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True})


@app.post("/api/run")
def run_now():
    body = request.json or {}
    mode = body.get("ip_mode", "both")
    top_n = str(body.get("top_n", 5))
    env = os.environ.copy()
    env["IP_MODE"] = mode
    env["TOP_N"] = top_n
    code, out, err = run("/usr/local/bin/cfip-run", env=env)
    return jsonify({"ok": code == 0, "code": code, "stdout": out[-4000:], "stderr": err[-4000:]})


@app.get("/api/logs")
def logs():
    if not LOG_FILE.exists():
        return jsonify({"ok": True, "logs": "(暂无日志)"})
    lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
    return jsonify({"ok": True, "logs": "\n".join(lines)})


@app.post("/api/cron")
def save_cron():
    body = request.json or {}
    content = (body.get("content", "") or "").strip() + "\n"
    if not content.strip():
        return jsonify({"ok": False, "error": "cron 内容不能为空"}), 400
    CRON_FILE.parent.mkdir(parents=True, exist_ok=True)
    CRON_FILE.write_text(content, encoding="utf-8")
    code, out, err = run(f"crontab {CRON_FILE}")
    return jsonify({"ok": code == 0, "stdout": out, "stderr": err})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "9527")))
