import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from web import app


class WebConfigTests(unittest.TestCase):
    def test_native_runner_and_log_paths_honor_environment(self):
        import os
        import runpy
        with mock.patch.dict(os.environ, {'RUN_CMD': '/native/bin/cfip-run',
                             'INIT_LOG_FILE': '/native/logs/init.log',
                             'BOOT_LOG_FILE': '/native/logs/boot.log'}):
            state = runpy.run_path(app.__file__, run_name='native_path_test')
        self.assertEqual(state['RUN_CMD'], '/native/bin/cfip-run')
        self.assertEqual(str(state['INIT_LOG_FILE']), '/native/logs/init.log')
        self.assertEqual(str(state['BOOT_LOG_FILE']), '/native/logs/boot.log')

    def test_missing_schedule_is_not_displayed_as_enabled(self):
        state = app.cron_state(app.default_cron_text())
        self.assertFalse(state['enabled'])
        self.assertFalse(state['reboot'])

    def test_sampling_settings_validate_persist_render_and_reach_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'config.json'
            with mock.patch.object(app, 'CONFIG_FILE', cfg), mock.patch.object(app, 'CRON_FILE', Path(tmp) / 'cron'):
                saved = app.save_config_from_form({'records_text': 'cf.example.com', 'advanced': {
                    'download_window': '8s', 'download_budget': '120s', 'max_scan_seconds': '240'}})
                self.assertEqual(saved['advanced']['download_window'], '8s')
                env = app.apply_advanced_env({})
                self.assertEqual((env['DOWNLOAD_WINDOW'], env['DOWNLOAD_BUDGET'], env['MAX_SCAN_SECONDS']),
                                 ('8s', '120s', '240'))
                page = app.render_index()
                self.assertIn('id="advDownloadWindow" value="8s"', page)
                self.assertIn('id="advDownloadBudget" value="120s"', page)
                self.assertNotIn('{{ADV_', page)

    def test_sampling_limits_reject_unbounded_or_invalid_values(self):
        for key, value in [('download_window', '0s'), ('download_window', '31s'),
                           ('download_budget', '1801s'), ('download_budget', '1ms'),
                           ('max_scan_seconds', '-1'), ('max_scan_seconds', '3601')]:
            advanced = dict(app.ADVANCED_DEFAULTS, **{key: value})
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                app.validate_advanced(advanced)

    def test_results_expose_per_family_time_without_dating_legacy_speed(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / 'old.json'
            new = Path(tmp) / 'new.json'
            old.write_text(json.dumps({'result': [{'ip': '192.0.2.1', 'download_mbps': 123}]}))
            new.write_text(json.dumps({'result': [{'ip': '2001:db8::1', 'download_ok': True,
                          'download_mbps': 4, 'download_measured_at': 100, 'download_method': 'window'}]}))
            with mock.patch.object(app, 'BEST_IP_FILES', {'4': old, '6': new}):
                items = app._load_best_ips_from_file()['items']
            self.assertIsNone(items[0]['download_measured_at'])
            self.assertEqual(items[1]['download_measured_at'], 100)
            self.assertEqual(items[0]['result_updated_at'], old.stat().st_mtime)
            failed = app.normalize_ip_row('4', {'ip': '192.0.2.2', 'download_ok': False, 'download_mbps': 999})
            self.assertIsNone(failed['download_mbps'])

    @unittest.skipUnless(shutil.which('node'), 'JavaScript check requires node')
    def test_browser_labels_legacy_speed_and_clears_empty_results(self):
        script = app.load_html().split('<script>', 1)[1].split('</script>', 1)[0]
        script = script.rsplit('\nreloadConfig();', 1)[0]
        harness = "const assert = require('node:assert/strict'); const root = {}; global.document = {getElementById: () => root};\n"
        assertions = r'''
renderBestIps([{ip: '192.0.2.1', version: '4', download_mbps: 123}]);
assert(root.innerHTML.includes('历史/未知时间，非本轮成绩'));
renderBestIps([{ip: '192.0.2.1', version: '4', download_mbps: 999, download_ok: false}]);
assert(!root.innerHTML.includes('下载 999'));
renderBestIps([]); assert.equal(root.textContent, '暂无已同步 IP');
'''
        result = subprocess.run(['node'], input=harness + script + assertions, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_blank_token_preserves_existing_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "config.json"
            config_file.write_text(json.dumps({
                "cloudflare": {
                    "token": "secret-token",
                    "records": [{"domain": "old.example.com", "zone_id": ""}],
                    "ttl": 60,
                    "proxied": False,
                }
            }), encoding="utf-8")
            with mock.patch.object(app, "CONFIG_FILE", config_file):
                saved = app.save_config_from_form({
                    "token": "",
                    "records_text": "new.example.com",
                    "ttl": 120,
                    "proxied": False,
                })
            self.assertEqual(saved["cloudflare"]["token"], "secret-token")
            self.assertEqual(saved["cloudflare"]["records"][0]["domain"], "new.example.com")

    def test_validation_accepts_zero_disabled_limits(self):
        advanced = dict(app.ADVANCED_DEFAULTS)
        advanced.update(download_top="0", min_download_mbps="0", max_loss_rate="0", timeout="500ms")
        app.validate_advanced(advanced)

    def test_validation_rejects_nonfinite_and_boolean_numbers(self):
        for value in (True, False, "NaN", "Infinity", -1, "1.5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.validated_number(value, "rounds", 1, 100, integer=True)

    def test_switch_timeout_is_bounded(self):
        advanced = dict(app.ADVANCED_DEFAULTS)
        for value in ('0.09', '60.01'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                advanced['switch_recheck_timeout'] = value
                app.validate_advanced(advanced)

    def test_export_uses_address_family_not_untrusted_version_label(self):
        with mock.patch.object(app, "get_selected_ips", return_value={"items": [
            {"version": "6", "ip": "1.1.1.1"},
            {"version": "4", "ip": "2606:4700:4700::1111"},
        ]}):
            self.assertEqual(app.export_results("txt", "4"), "1.1.1.1\n")
            self.assertEqual(app.export_results("txt", "6"), "2606:4700:4700::1111\n")

    def test_manual_run_settings_render_independently_and_explain_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "config.json"
            config_file.write_text(json.dumps({"run_settings": {"ip_mode": "6", "top_n": 23},
                                               "advanced": {"download_top": "11"}}))
            cron_file = Path(tmp) / "cron"
            cron_file.write_text(app.build_cron_text(True, 6, False, "4", 3))
            with mock.patch.object(app, "CONFIG_FILE", config_file), mock.patch.object(app, "CRON_FILE", cron_file):
                page = app.render_index()
            self.assertIn('<option value="6" selected>IPv6</option>', page)
            self.assertIn('id="topN" type="number" min="1" max="50" value="23"', page)
            self.assertIn('id="scheduleTopN" type="number" min="1" max="50" value="3"', page)
            self.assertIn('id="advDownloadTop" type="number" min="0" value="11"', page)
            self.assertIn('保存运行设置', page)
            self.assertIn('每个 IP 地址族的上限', page)
            self.assertIn('手动运行设置与定时计划相互独立', page)
            self.assertIn('固定候选数', page)
            self.assertIn('下载成功数', page)
            self.assertIn('两个模式均逐 IP 串行', page)
            self.assertNotIn('并发测速', page)
            self.assertNotIn('{{RUN_', page)
            self.assertIn('applyRunSettings(form.run_settings', page)
            self.assertIn("fetch('/api/run-settings'", page)

    def test_legacy_config_gets_separate_manual_defaults(self):
        state = app.form_state({"cloudflare": {}, "advanced": {"download_top": "21"}})
        self.assertEqual(state["run_settings"], {"ip_mode": "both", "top_n": 5})
        self.assertEqual(app.default_config()["run_settings"], {"ip_mode": "both", "top_n": 5})
        for settings in (None, [], {"ip_mode": "7", "top_n": 0}):
            with self.subTest(settings=settings):
                self.assertEqual(app.form_state({"run_settings": settings})["run_settings"],
                                 {"ip_mode": "both", "top_n": 5})

    @unittest.skipUnless(shutil.which("node"), "JavaScript regression check requires node")
    def test_dynamic_run_settings_load_save_and_run(self):
        script = app.load_html().split("<script>", 1)[1].split("</script>", 1)[0]
        script = script.rsplit("\nreloadConfig();", 1)[0]
        harness = r'''
const assert = require('node:assert/strict');
const elements = new Map();
global.document = {getElementById(id) {
  if (!elements.has(id)) elements.set(id, {value: '', checked: false});
  return elements.get(id);
}};
global.alert = () => {};
global.setTimeout = () => {};
let saved = {ip_mode: '6', top_n: 17};
let runPayload;
global.fetch = async (path, options) => {
  if (path === '/api/config') return {json: async () => ({ok: true, form: {run_settings: saved}})};
  const payload = JSON.parse(options.body);
  if (path === '/api/run-settings') saved = payload;
  else if (path === '/api/run') {runPayload = payload; saved = {ip_mode: payload.ip_mode, top_n: payload.top_n};}
  else throw Error('unexpected fetch ' + path);
  return {json: async () => ({ok: true, run_settings: saved})};
};
'''
        assertions = r'''
loadStatus = async () => {};
(async () => {
  await reloadConfig();
  assert.equal(document.getElementById('ipMode').value, '6');
  assert.equal(document.getElementById('topN').value, 17);
  document.getElementById('ipMode').value = '4';
  document.getElementById('topN').value = '21';
  await saveRunSettings();
  assert.deepEqual(saved, {ip_mode: '4', top_n: 21});
  applyRunSettings({ip_mode: 'both', top_n: 5});
  await reloadConfig();
  assert.equal(document.getElementById('topN').value, 21);
  assert.equal(document.getElementById('ipMode').value, '4');
  document.getElementById('scanOnly').checked = true;
  await runNow();
  assert.deepEqual(runPayload, {ip_mode: '4', top_n: 21, scan_only: true});
  document.getElementById('topN').value = '';
  assert.equal(collectRunSettings().top_n, 0); // Invalid blank must not silently become 5.
  console.log('dynamic run settings verified');
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
        result = subprocess.run(["node"], input=harness + script + assertions,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dynamic run settings verified", result.stdout)

    def test_form_state_never_returns_cloudflare_token(self):
        state = app.form_state({"cloudflare": {"token": "form-secret"}})
        self.assertEqual(state["token"], "")
        self.assertTrue(state["token_configured"])
        self.assertNotIn("form-secret", json.dumps(state))

    def test_web_bind_host_default_and_environment_override(self):
        import os
        import runpy
        import threading
        with mock.patch.object(threading.Thread, "start"), \
             mock.patch("http.server.ThreadingHTTPServer") as server:
            for environ, expected in (({}, "0.0.0.0"), ({"WEB_BIND_HOST": "192.0.2.15"}, "192.0.2.15")):
                with self.subTest(environ=environ), tempfile.TemporaryDirectory() as tmp, \
                     mock.patch.dict(os.environ, {"PROJECT_DIR": tmp, "PORT": "19527", **environ}):
                    if not environ:
                        os.environ.pop("WEB_BIND_HOST", None)
                    runpy.run_path(app.__file__, run_name="__main__")
                    self.assertEqual(server.call_args.args[0], (expected, 19527))
                    server.return_value.serve_forever.assert_called()

    def test_rendered_page_does_not_embed_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "config.json"
            config_file.write_text(json.dumps({
                "cloudflare": {
                    "token": "must-not-leak",
                    "records": [{"domain": "cf.example.com", "zone_id": ""}],
                    "ttl": 60,
                    "proxied": False,
                }
            }), encoding="utf-8")
            with mock.patch.object(app, "CONFIG_FILE", config_file), \
                 mock.patch.object(app, "CRON_FILE", Path(tmp) / "cron"):
                page = app.render_index()
            self.assertNotIn("must-not-leak", page)


if __name__ == "__main__":
    unittest.main()
