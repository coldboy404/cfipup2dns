#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(os.getenv("PROJECT_DIR", "/data/project"))
CONFIG_FILE = Path(os.getenv("CONFIG_FILE", str(PROJECT_DIR / "config.json")))
GH_PROXY = os.getenv("GH_PROXY", "https://gh-proxy.org/")
MCIS_TAG = os.getenv("MCIS_TAG", "v0.2.3")
IP_MODE = os.getenv("IP_MODE", "both")
TOP_N = int(os.getenv("TOP_N", "5"))
TOP_TEST = int(os.getenv("TOP_TEST", "50"))
CONCURRENCY = os.getenv("CONCURRENCY", "200")
TIMEOUT = os.getenv("TIMEOUT", "3s")
ROUNDS = os.getenv("ROUNDS", "4")
BUDGET = os.getenv("BUDGET", "3000")
DOWNLOAD_TOP = os.getenv("DOWNLOAD_TOP", "20")
DOWNLOAD_TIMEOUT = os.getenv("DOWNLOAD_TIMEOUT", "45s")
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
            log(f"[*] download: {url}")
            fetch(url, dest, timeout=timeout)
            return
        except Exception as e:
            last_err = e
            log(f"[!] download failed: {e}")
    raise last_err


def ensure_mcis():
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    arch = os.uname().machine
    if arch in ("x86_64", "amd64"):
        mcis_arch = "amd64"
    elif arch in ("aarch64", "arm64"):
        mcis_arch = "arm64"
    else:
        raise RuntimeError(f"unsupported arch: {arch}")

    mcis_bin = PROJECT_DIR / "montecarlo-ip-searcher"
    if not mcis_bin.exists():
        pkg = f"mcis-{MCIS_TAG}-linux-{mcis_arch}.tar.gz"
        raw_url = f"https://github.com/Leo-Mu/montecarlo-ip-searcher/releases/download/{MCIS_TAG}/{pkg}"
        with tempfile.TemporaryDirectory() as td:
            tgz = Path(td) / pkg
            fetch_with_fallback(raw_url, tgz)
            with tarfile.open(tgz, "r:gz") as tar:
                tar.extractall(PROJECT_DIR)
            src = PROJECT_DIR / "mcis"
            if src.exists():
                src.rename(mcis_bin)
            mcis_bin.chmod(0o755)

    for name in ("ipv4cidr.txt", "ipv6cidr.txt"):
        p = PROJECT_DIR / name
        if not p.exists() or p.stat().st_size == 0:
            raw = f"https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/main/{name}"
            fetch_with_fallback(raw, p, timeout=60)

    return mcis_bin


def load_config():
    if not CONFIG_FILE.exists():
        raise RuntimeError(f"config file not found: {CONFIG_FILE}")
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
        uniq.append(row)
    dl = [r for r in uniq if float(r.get("download_mbps") or 0) > 0]
    if dl:
        dl.sort(key=lambda x: float(x.get("download_mbps") or 0), reverse=True)
        return "download_mbps", dl[:TOP_N]
    scored = [r for r in uniq if float(r.get("score_ms") or 0) > 0]
    scored.sort(key=lambda x: float(x.get("score_ms") or 0))
    return "score_ms", scored[:TOP_N]


def run_mode(mode, cfg, mcis_bin):
    dns_type = "A" if mode == "4" else "AAAA"
    cidr_file = PROJECT_DIR / ("ipv4cidr.txt" if mode == "4" else "ipv6cidr.txt")
    if not cidr_file.exists():
        log(f"[!] skip ipv{mode}: missing {cidr_file}")
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
        if DOWNLOAD_URL:
            args += ["-download-url", DOWNLOAD_URL]
        if DOWNLOAD_BYTES:
            args += ["-download-bytes", DOWNLOAD_BYTES]

    log(f"[*] run mcis ipv{mode}")
    with open(result_file, "w", encoding="utf-8") as out:
        try:
            subprocess.run(args, stdout=out, stderr=subprocess.STDOUT, check=True, timeout=MAX_SCAN_SECONDS or None)
        except subprocess.TimeoutExpired:
            log(f"[*] mcis timeout after {MAX_SCAN_SECONDS}s, continue with partial result")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"mcis failed for ipv{mode}: {e.returncode}")

    sort_mode, best = parse_json_lines(result_file)
    if not best:
        raise RuntimeError(f"no valid ips found for ipv{mode}")

    token = cfg["cloudflare"]["token"]
    zone_id = cfg["cloudflare"]["zone_id"]
    domain = cfg["cloudflare"]["domain"]
    ttl = cfg["cloudflare"].get("ttl", 60)
    proxied = cfg["cloudflare"].get("proxied", False)

    q = urllib.parse.urlencode({"type": dns_type, "name": domain})
    existing = cf_request("GET", f"/zones/{zone_id}/dns_records?{q}", token)
    for rec in existing.get("result", []):
        rid = rec.get("id")
        if rid:
            cf_request("DELETE", f"/zones/{zone_id}/dns_records/{rid}", token)

    for row in best:
        cf_request("POST", f"/zones/{zone_id}/dns_records", token, {
            "type": dns_type,
            "name": domain,
            "content": row["ip"],
            "ttl": ttl,
            "proxied": proxied,
        })

    (PROJECT_DIR / f"best_ips_v{mode}.json").write_text(json.dumps({"mode": sort_mode, "result": best}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[✓] ipv{mode} updated {len(best)} records by {sort_mode}")


def main():
    cfg = load_config()
    mcis_bin = ensure_mcis()
    modes = ["4", "6"] if IP_MODE == "both" else [IP_MODE]
    for mode in modes:
        if mode not in ("4", "6"):
            raise RuntimeError(f"invalid IP_MODE: {IP_MODE}")
        run_mode(mode, cfg, mcis_bin)
    log("[✓] done")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
        print(f"[HTTPError] {e} {body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
