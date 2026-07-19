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
