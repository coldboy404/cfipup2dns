import ast
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit


class AccelerationTests(unittest.TestCase):
    def fetch_function(self):
        script = (Path(__file__).resolve().parents[1] / 'install.sh').read_text()
        tree = ast.parse(script.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0])
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'fetch')
        namespace = dict(os=os, subprocess=subprocess, urlsplit=urlsplit, sys=__import__('sys'))
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<installer-fetch>', 'exec'), namespace)
        return namespace['fetch']

    def test_proxy_then_backup_then_direct(self):
        fetch = self.fetch_function()
        url = 'https://api.github.com/repos/coldboy404/cfipup2dns/releases/latest'
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CFIP_GITHUB_PROXY': 'https://gh-proxy.com/'}), \
                patch('subprocess.run', side_effect=[subprocess.CalledProcessError(22, 'curl'), subprocess.TimeoutExpired('curl', 100), None]) as run:
            fetch(url, Path(tmp) / 'download')
        self.assertEqual([call.args[0][-3] for call in run.call_args_list],
                         ['https://gh-proxy.com/' + url, 'https://gh-proxy.org/' + url, url])

    def test_direct_and_custom_base_not_wrapped(self):
        fetch = self.fetch_function()
        for url, proxy in [('https://github.com/a/b/releases/file', 'direct'),
                           ('http://127.0.0.1:1234/download', 'https://gh-proxy.com/')]:
            with patch.dict(os.environ, {'CFIP_GITHUB_PROXY': proxy}), patch('subprocess.run') as run:
                fetch(url, Path('unused-fixture'))
                self.assertEqual(run.call_args.args[0][-3], url)
                self.assertEqual(run.call_count, 1)

    def test_unsafe_proxy_rejected_before_download(self):
        with patch.dict(os.environ, {'CFIP_GITHUB_PROXY': 'http://user:password@proxy/'}), \
                patch('subprocess.run') as run, self.assertRaises(SystemExit):
            self.fetch_function()('https://github.com/a/b', Path('unused-fixture'))
        run.assert_not_called()
