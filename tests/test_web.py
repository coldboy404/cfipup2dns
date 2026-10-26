import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from web import app


class WebConfigTests(unittest.TestCase):
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
