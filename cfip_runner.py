#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
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
DOWNLOAD_TOP = os.getenv("DOWNLOAD_TOP", "20")
DOWNLOAD_TIMEOUT = os.getenv("DOWNLOAD_TIMEOUT", "45s")
DOWNLOAD_MODE = os.getenv("DOWNLOAD_MODE", "sequential")
DOWNLOAD_URL = os.getenv("DOWNLOAD_URL", "")
DOWNLOAD_BYTES = os.getenv("DOWNLOAD_BYTES", "50000000")
MAX_SCAN_SECONDS = int(os.getenv("MAX_SCAN_SECONDS", "0"))


def log(msg):
    print(msg, flush=True)


def fetch(url, dest, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "cfipup2dns/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


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
    with urllib.request.urlopen(req, timeout=60) as resp:
        obj = json.loads(resp.read().decode("utf-8"))
    if not obj.get("success"):
        raise RuntimeError(json.dumps(obj, ensure_ascii=False))
    return obj


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

        # latency_ms 在上游不一定存在，兜底 total/ttfb/connect；<=0 一律视为无效
        latency = row.get("latency_ms")
        if latency in (None, "", 0, "0"):
            latency = row.get("total_ms") or row.get("ttfb_ms") or row.get("connect_ms")
        try:
            latency = float(latency)
            row["latency_ms"] = latency if latency > 0 else None
        except Exception:
            row["latency_ms"] = None

        # 统一布尔化，避免字符串/数字混用导致误判
        row_ok = row.get("ok")
        if isinstance(row_ok, str):
            row_ok = row_ok.strip().lower() in ("1", "true", "yes", "ok")
        row["ok"] = bool(row_ok)

        uniq.append(row)

    # 优先使用下载测速成功的结果（与上游 DNS 上传逻辑一致：只认 download_ok/download_mbps）
    dl = [
        r for r in uniq
        if r.get("ok") and float(r.get("download_mbps") or 0) > 0
    ]
    if dl:
        dl.sort(key=lambda x: float(x.get("download_mbps") or 0), reverse=True)
        return "download_mbps", dl[:TOP_N]

    # 回退展示：保留可探测结果，便于页面排障（即使下载未测出）
    scored = [
        r for r in uniq
        if r.get("ok") and float(r.get("score_ms") or 0) > 0
    ]
    scored.sort(key=lambda x: float(x.get("score_ms") or 0))
    return "score_ms", scored[:TOP_N]


def run_mode(mode, cfg, mcis_bin):
    dns_type = "A" if mode == "4" else "AAAA"
    cidr_file = PROJECT_DIR / ("ipv4cidr.txt" if mode == "4" else "ipv6cidr.txt")
    if not cidr_file.exists():
        log(f"[!] 跳过 IPv{mode}: 缺少 {cidr_file}")
        return

    result_file = PROJECT_DIR / f"scan_results_v{mode}.log"
    args = [
        str(mcis_bin),
        "-cidr-file", str(cidr_file),
        "-concurrency", CONCURRENCY,
        "-top", str(max(TOP_TEST, TOP_N)),
        "-out", "jsonl",
        "-v",
        "-budget", BUDGET,
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
    log(f"[*] 并发: {CONCURRENCY}，轮次: {ROUNDS}，预算: {BUDGET}，测速模式: {DOWNLOAD_MODE}")
    with open(result_file, "w", encoding="utf-8") as out:
        try:
            subprocess.run(args, stdout=out, stderr=subprocess.STDOUT, check=True, timeout=MAX_SCAN_SECONDS or None)
        except subprocess.TimeoutExpired:
            log(f"[!] 扫描超时，已按 {MAX_SCAN_SECONDS}s 截止，继续读取已有结果")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"mcis 执行失败，IPv{mode}，退出码: {e.returncode}")

    sort_mode, best = parse_json_lines(result_file)
    if not best:
        raise RuntimeError(f"IPv{mode} 没有找到可用 IP")

    zero_speed = sum(1 for row in best if float(row.get("download_mbps") or 0) <= 0)
    if zero_speed:
        log(f"[!] IPv{mode} 有 {zero_speed} 个结果未测到下载速度，通常表示测速未成功，不一定代表 IP 不可用")

    missing_latency = sum(1 for row in best if not row.get("latency_ms"))
    if missing_latency:
        log(f"[!] IPv{mode} 有 {missing_latency} 个结果缺少延迟字段，已尝试用 total_ms / ttfb_ms / connect_ms 兜底")

    token = cfg["cloudflare"]["token"]
    zone_id = cfg["cloudflare"]["zone_id"]
    domain = cfg["cloudflare"]["domain"]
    ttl = cfg["cloudflare"].get("ttl", 60)
    proxied = cfg["cloudflare"].get("proxied", False)

    q = urllib.parse.urlencode({"type": dns_type, "name": domain})
    log(f"[*] 正在清理旧的 {dns_type} 记录")
    existing = cf_request("GET", f"/zones/{zone_id}/dns_records?{q}", token)
    for rec in existing.get("result", []):
        rid = rec.get("id")
        if rid:
            cf_request("DELETE", f"/zones/{zone_id}/dns_records/{rid}", token)

    log(f"[*] 正在写入新的 {dns_type} 记录，共 {len(best)} 条")
    for row in best:
        cf_request("POST", f"/zones/{zone_id}/dns_records", token, {
            "type": dns_type,
            "name": domain,
            "content": row["ip"],
            "ttl": ttl,
            "proxied": proxied,
        })

    (PROJECT_DIR / f"best_ips_v{mode}.json").write_text(json.dumps({"mode": sort_mode, "result": best}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[✓] IPv{mode} 已更新 {len(best)} 条 DNS 记录，排序依据: {sort_mode}")


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
