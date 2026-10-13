import base64
import csv
import http.client
import io
import json
import os
import signal
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from web import app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patches = [
            mock.patch.dict(os.environ, {"WEB_USERNAME": "", "WEB_PASSWORD": ""}),
            mock.patch.object(app, "PROJECT_DIR", self.root),
            mock.patch.object(app, "CONFIG_FILE", self.root / "config.json"),
            mock.patch.object(app, "CRON_FILE", self.root / "cron"),
            mock.patch.object(app, "LOG_FILE", self.root / "log"),
            mock.patch.object(app, "INIT_LOG_FILE", self.root / "init"),
            mock.patch.object(app, "BOOT_LOG_FILE", self.root / "boot"),
            mock.patch.object(app, "BEST_IP_FILES", {"4": self.root / "v4.json", "6": self.root / "v6.json"}),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        app.LAST_RUN.update(running=False, cancelled=False)
        app.DNS_CACHE.update(items=[], fetched_at=None, error=None)
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.addCleanup(self.finish_job)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def finish_job(self):
        app.stop_run()
        if app.RUN_THREAD:
            app.RUN_THREAD.join(5)

    def request(self, method, path, body=None, headers=None):
        headers = dict(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body)
            headers.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            conn.close()

    def test_auth_health_and_secret_masking(self):
        app.save_config_from_form({"token": "hidden-token", "records_text": "cf.example.com"})
        with mock.patch.dict(os.environ, {"WEB_USERNAME": "admin", "WEB_PASSWORD": "secret"}):
            status, _, body = self.request("GET", "/api/health")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"ok": True, "service": "cfipup2dns"})
            for method, path in (("GET", "/"), ("GET", "/api/config"), ("GET", "/api/export"), ("POST", "/api/run")):
                self.assertEqual(self.request(method, path)[0], 401)
            for authorization in ("Basic !!", "Bearer abc", "Basic " + base64.b64encode(b"admin:wrong").decode()):
                self.assertEqual(self.request("GET", "/", headers={"Authorization": authorization})[0], 401)
            auth = {"Authorization": "Basic " + base64.b64encode(b"admin:secret").decode()}
            for path in ("/", "/api/config"):
                status, _, body = self.request("GET", path, headers=auth)
                self.assertEqual(status, 200)
                self.assertNotIn("hidden-token", body)
            status, _, body = self.request("POST", "/api/config", {"token": "", "records_text": "new.example.com"}, auth)
            self.assertEqual(status, 200)
            self.assertNotIn("hidden-token", body)
            self.assertEqual(app.load_config()["cloudflare"]["token"], "hidden-token")
        with mock.patch.dict(os.environ, {"WEB_USERNAME": "admin", "WEB_PASSWORD": ""}):
            self.assertEqual(self.request("GET", "/")[0], 401)

    def test_mutation_origin_and_content_limits(self):
        with mock.patch.object(app, "trigger_run", return_value=True) as trigger:
            for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Origin": "http://["}, {"Sec-Fetch-Site": "cross-site"}):
                self.assertEqual(self.request("POST", "/api/run", {}, headers)[0], 403)
            trigger.assert_not_called()
            for origin in (f"http://127.0.0.1:{self.server.server_port}", f"https://127.0.0.1:{self.server.server_port}"):
                self.assertEqual(self.request("POST", "/api/run", {}, {"Origin": origin})[0], 200)
            # Non-browser callers without Origin remain compatible.
            self.assertEqual(self.request("POST", "/api/run", {})[0], 200)
        cases = [
            ("{}", {"Content-Length": str(app.MAX_REQUEST_BYTES + 1)}, 413),
            ("{}", {"Content-Length": "-1"}, 400),
            ("{}", {"Content-Length": "oops"}, 400),
            ("{}", {"Transfer-Encoding": "chunked"}, 400),
            ("{}", {"Content-Type": "text/plain"}, 415),
            ("[1]", {"Content-Type": "application/json"}, 400),
            ("{", {"Content-Type": "application/json"}, 400),
        ]
        for body, headers, expected in cases:
            with self.subTest(headers=headers, body=body):
                self.assertEqual(self.request("POST", "/api/config", body, headers)[0], expected)
        self.assertFalse(app.CONFIG_FILE.exists())

    def test_numeric_validation_does_not_write_config(self):
        cases = [{"ttl": 0}, {"ttl": "bad"}, {"ttl": 1.5}, {"advanced": []}]
        for key, value in (("rounds", 0), ("rounds", "1.5"), ("download_top", -1), ("timeout", "0s"), ("timeout", "no"), ("download_timeout", "100h"), ("max_loss_rate", "NaN"), ("max_loss_rate", 2), ("min_download_mbps", "inf"), ("download_mode", "bad")):
            cases.append({"advanced": {key: value}})
        cases.append({"advanced": {"max_latency_ms": "10", "min_latency_ms": "20"}})
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.request("POST", "/api/config", body)[0], 400)
                self.assertFalse(app.CONFIG_FILE.exists())
        self.assertEqual(self.request("POST", "/api/config", {"advanced": {"download_top": 0, "timeout": "1m30s"}})[0], 200)
        for path, body in (("/api/run", {"top_n": 0}), ("/api/run", {"ip_mode": "7"}), ("/api/schedule", {"interval_hours": 25})):
            self.assertEqual(self.request("POST", path, body)[0], 400)

    def test_server_generated_csv_txt_export(self):
        (self.root / "v4.json").write_text(json.dumps({"result": [
            {"ip": "1.1.1.1", "latency_ms": 12, "download_mbps": 90},
            {"ip": "1.1.1.1"}, {"ip": "=formula"},
            {"ip": "8.8.8.8", "latency_ms": "=HYPERLINK(bad)", "download_mbps": "NaN"},
        ]}))
        (self.root / "v6.json").write_text(json.dumps({"result": [{"ip": "2606:4700:4700::1111"}]}))
        status, headers, body = self.request("GET", "/api/export?format=csv&ip_mode=4")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="best-ips-4.csv"')
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        rows = list(csv.DictReader(io.StringIO(body)))
        self.assertEqual([r["ip"] for r in rows], ["1.1.1.1", "8.8.8.8"])
        self.assertEqual(rows[1]["latency_ms"], "")
        self.assertNotIn("formula", body)
        for mode, expected in (("6", "2606:4700:4700::1111\n"), ("both", "1.1.1.1\n8.8.8.8\n2606:4700:4700::1111\n")):
            self.assertEqual(self.request("GET", f"/api/export?format=txt&ip_mode={mode}")[2], expected)
        self.assertEqual(self.request("GET", "/api/export?format=../../config.json")[0], 400)
        self.assertEqual(self.request("GET", "/api/export?ip_mode=7")[0], 400)

    def test_scan_only_api_and_persisted_history(self):
        with mock.patch.object(app, "trigger_run", return_value=True) as trigger:
            self.assertEqual(self.request("POST", "/api/run", {"scan_only": True})[0], 200)
            self.assertEqual(trigger.call_args.args[0]["SCAN_ONLY"], "true")
            self.assertEqual(self.request("POST", "/api/run", {})[0], 200)
            self.assertEqual(trigger.call_args.args[0]["SCAN_ONLY"], "false")
            self.assertEqual(self.request("POST", "/api/run", {"scan_only": "false"})[0], 400)
        summary = {"status": "no_new_results", "results": [{"status": "kept_old"}]}
        (self.root / "run_history.json").write_text(json.dumps([summary]))
        (self.root / "scan_only_v4.json").write_text(json.dumps({"result": [{"ip": "1.1.1.1"}]}))
        self.assertEqual(json.loads(self.request("GET", "/api/history")[2])["history"], [summary])
        self.assertEqual(json.loads(self.request("GET", "/api/status")[2])["last_summary"], summary)
        self.assertEqual(json.loads(self.request("GET", "/api/scan-results")[2])["result"][0]["ip"], "1.1.1.1")
        with mock.patch.dict(os.environ, {"WEB_USERNAME": "admin", "WEB_PASSWORD": "secret"}):
            for path in ("/api/history", "/api/scan-results"):
                self.assertEqual(self.request("GET", path)[0], 401)

    def test_empty_export(self):
        self.assertEqual(self.request("GET", "/api/export?format=txt")[2], "")
        self.assertEqual(len(self.request("GET", "/api/export")[2].splitlines()), 1)

    def test_spawn_failure_releases_reservation(self):
        with mock.patch.object(app, "RUN_CMD", str(self.root / "missing")):
            self.assertTrue(app.trigger_run())
            app.RUN_THREAD.join(3)
            self.assertFalse(app.LAST_RUN["running"])
            self.assertEqual(app.LAST_RUN["code"], -1)
            self.assertTrue(app.LAST_RUN["stderr"])

    def test_atomic_start_and_stop_before_spawn(self):
        entered = threading.Event()
        release = threading.Event()
        original = app.run_job
        def delayed(env):
            entered.set()
            release.wait(3)
            original(env)
        with mock.patch.object(app, "run_job", side_effect=delayed), mock.patch.object(app.subprocess, "Popen") as spawn:
            with ThreadPoolExecutor(max_workers=16) as pool:
                starts = list(pool.map(lambda _: app.trigger_run(), range(32)))
            self.assertEqual(sum(starts), 1)
            self.assertTrue(entered.wait(1))
            self.assertTrue(app.stop_run())
            release.set()
            app.RUN_THREAD.join(3)
            spawn.assert_not_called()
            self.assertFalse(app.LAST_RUN["running"])
            self.assertTrue(app.LAST_RUN["cancelled"])

    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    def test_http_stop_kills_shell_runner_and_child_without_later_writes(self):
        # Real shell -> Python runner -> Python mcis-like descendant. Both would
        # write a DNS marker if cancellation only stopped the shell or child.
        child = self.root / "child.py"
        runner = self.root / "runner.py"
        command = self.root / "cfip.sh"
        marker = self.root / "dns-write"
        ready = self.root / "ready"
        child.write_text("import os,time\nfrom pathlib import Path\n"
                         f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
                         "time.sleep(1.5)\n"
                         f"Path({str(marker)!r}).write_text('child DNS')\n")
        runner.write_text("import subprocess,sys\nfrom pathlib import Path\n"
                          f"subprocess.run([sys.executable, {str(child)!r}])\n"
                          f"Path({str(marker)!r}).write_text('runner DNS')\n")
        command.write_text(f"#!/bin/sh\npython3 {runner}\n")
        command.chmod(0o700)
        with mock.patch.object(app, "RUN_CMD", str(command)):
            self.assertEqual(self.request("POST", "/api/run", {})[0], 200)
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists())
            self.assertEqual(self.request("POST", "/api/run", {})[0], 409)
            self.assertEqual(self.request("POST", "/api/stop")[0], 200)
            app.RUN_THREAD.join(3)
            self.assertFalse(app.LAST_RUN["running"])
            self.assertTrue(app.LAST_RUN["cancelled"])
            self.assertEqual(app.LAST_RUN["code"], -signal.SIGKILL)
            time.sleep(1.6)
            self.assertFalse(marker.exists())
            self.assertEqual(self.request("POST", "/api/stop")[0], 409)
            self.assertEqual(self.request("POST", "/api/run", {})[0], 200)
            self.assertEqual(self.request("POST", "/api/stop")[0], 200)
            app.RUN_THREAD.join(3)


if __name__ == "__main__":
    unittest.main()
