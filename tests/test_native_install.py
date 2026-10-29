"""Rootless install integration tests: local release HTTP, fake systemd, no host writes."""
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import unittest

REPO = Path(__file__).resolve().parents[1]
FAKE_SYSTEMCTL = '''#!/usr/bin/env python3
import os, sys
from pathlib import Path
root = Path(os.environ['CFIP_INSTALL_ROOT'])
with (root / 'systemctl.calls').open('a') as f: f.write(' '.join(sys.argv[1:])+'\\n')
args = sys.argv[1:]
active = root / 'active'
if args[0] == 'show': sys.exit(0)
if args[0] == 'is-enabled': sys.exit(0 if (root/'enabled').exists() else 1)
if args[0] == 'is-active': sys.exit(0 if active.exists() else 1)
if args[0] in ('restart','start'):
    if os.environ.get('CFIP_TEST_FAIL_START') and args[0] == 'restart': sys.exit(1)
    active.touch()
if args[0] in ('stop','disable'): active.unlink(missing_ok=True)
if args[0] == 'enable': (root/'enabled').touch()
'''


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == '/api/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        else:
            super().do_GET()


class NativeInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cfip-native-tests-')
        self.addCleanup(self.tmp.cleanup)
        self.tmpdir = Path(self.tmp.name)
        self.root = self.tmpdir / 'root'
        self.root.mkdir()
        self.assets = self.tmpdir / 'assets'
        self.assets.mkdir()
        self.source = self.tmpdir / 'source'
        self.source.mkdir()
        for name in ('install.sh', 'cfip_runner.py', 'speed_probe.py', 'config.example.json'):
            shutil.copy2(REPO / name, self.source / name)
        for name in ('native', 'web'):
            shutil.copytree(REPO / name, self.source / name, ignore=shutil.ignore_patterns('__pycache__'))
        (self.source / 'docker').mkdir()
        shutil.copy2(REPO / 'docker/sync_cidr.py', self.source / 'docker/sync_cidr.py')
        bundled = self.source / 'bundled-mcis'
        bundled.mkdir()
        (bundled / '.mcis_version').write_text('add8ab94463ec440d17d354c858be7346a52b95e+source')
        (bundled / 'montecarlo-ip-searcher').write_text('#!/bin/sh\nexit 0\n')
        (bundled / 'montecarlo-ip-searcher').chmod(0o755)
        (bundled / 'ipv4cidr.txt').write_text('192.0.2.0/24\n')
        (bundled / 'ipv6cidr.txt').write_text('2001:db8::/32\n')
        fakebin = self.tmpdir / 'bin'
        fakebin.mkdir()
        (fakebin / 'systemctl').write_text(FAKE_SYSTEMCTL)
        (fakebin / 'systemctl').chmod(0o755)
        handler = functools.partial(QuietHandler, directory=str(self.assets))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port
        self.env = dict(os.environ, CFIP_INSTALL_ROOT=str(self.root), CFIP_BASE_URL=f'http://127.0.0.1:{self.port}',
                        PATH=str(fakebin) + ':' + os.environ['PATH'])
        self.env.pop('CFIP_VERSION', None)
        self.env['WEB_BIND_HOST'] = '127.0.0.1'
        self.env['PORT'] = str(self.port)
        # fake systemd health server already listens here, so mark native unit
        # active only AFTER port preflight by choosing a separate free bind port
        # and forwarding health via fake systemctl startup below.
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.bind_port = sock.getsockname()[1]
        self.env['PORT'] = str(self.bind_port)
        self.health_server = None
        # fake systemctl starts a real HTTP health child on restart/start.
        fake = (fakebin / 'systemctl').read_text().replace("    active.touch()", """    import subprocess
    if not active.exists():
        cmd = [sys.executable, str(root/'health.py')]
        child = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        (root/'health.pid').write_text(str(child.pid))
    active.touch()""").replace("if args[0] in ('stop','disable'): active.unlink(missing_ok=True)", """if args[0] in ('stop','disable'):
    import signal, time
    if (root/'health.pid').exists():
        try: os.kill(int((root/'health.pid').read_text()), signal.SIGTERM)
        except ProcessLookupError: pass
        (root/'health.pid').unlink()
        time.sleep(.05)
    active.unlink(missing_ok=True)""")
        (fakebin / 'systemctl').write_text(fake)
        (self.root / 'health.py').write_text('''from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'{"ok":true}')
HTTPServer(('127.0.0.1', %d), H).serve_forever()
''' % self.bind_port)
        self.addCleanup(self.stop_health)
        self.make_release('v2.1.0')
        (self.assets / 'latest').write_text(json.dumps({'tag_name': 'v2.1.0', 'draft': False, 'prerelease': False}))

    def stop_health(self):
        subprocess.run(['systemctl', 'stop', 'cfipup2dns.service'], env=self.env, capture_output=True)

    def make_release(self, version, bad_member=None):
        directory = self.assets / 'download' / version
        directory.mkdir(parents=True, exist_ok=True)
        for arch in ('amd64', 'arm64'):
            target = directory / f'cfipup2dns-linux-{arch}.tar.gz'
            with tarfile.open(target, 'w:gz') as archive:
                for path in sorted(self.source.rglob('*')):
                    if path.is_file():
                        archive.add(path, arcname=str(path.relative_to(self.source)), recursive=False)
                if bad_member:
                    info = tarfile.TarInfo(bad_member)
                    info.size = 1
                    archive.addfile(info, io.BytesIO(b'x'))
            with (directory / 'SHA256SUMS').open('a') as sums:
                sums.write(hashlib.sha256(target.read_bytes()).hexdigest() + '  ' + target.name + '\n')
        return directory

    def run_install(self, *args, ok=True, **extra_env):
        result = subprocess.run(['bash', str(REPO / 'install.sh'), *args], env=dict(self.env, **extra_env), text=True, capture_output=True, timeout=30)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def test_install_rerun_update_keep_config_credentials_custom_cidr(self):
        first = self.run_install()
        self.assertIn('Initial password:', first.stdout)
        data = self.root / 'var/lib/cfipup2dns'
        env = (data / 'native.env').read_bytes()
        self.assertEqual((data / 'native.env').stat().st_mode & 0o777, 0o600)
        project = data / 'project'
        (project / 'config.json').write_text('{"custom":true}')
        (project / 'ipv4cidr.txt').write_text('198.51.100.0/24\n')
        rerun = self.run_install()
        self.assertNotIn('Initial password:', rerun.stdout)
        self.make_release('v2.1.1')
        update = self.run_install('update', CFIP_VERSION='v2.1.1', PORT='1', WEB_BIND_HOST='0.0.0.0')
        self.assertNotIn('Initial password:', update.stdout)
        self.assertEqual((data / 'native.env').read_bytes(), env)
        self.assertEqual((project / 'config.json').read_text(), '{"custom":true}')
        self.assertEqual((project / 'ipv4cidr.txt').read_text(), '198.51.100.0/24\n')
        self.assertEqual((self.root / 'opt/cfipup2dns/current').resolve().name, 'v2.1.1')
        self.assertFalse((data / 'cron/cfip.cron').exists())
        calls = (self.root / 'systemctl.calls').read_text()
        self.assertNotIn('cron', calls)

    def test_checksum_rejected_before_host_changes(self):
        for path in (self.assets / 'download/v2.1.0').glob('*.tar.gz'):
            with path.open('ab') as out:
                out.write(b'corrupt')
        result = self.run_install(ok=False)
        self.assertIn('SHA256 mismatch', result.stderr)
        self.assertFalse((self.root / 'opt').exists())

    def test_traversal_rejected(self):
        self.make_release('v2.1.1', '../../escape')
        self.run_install(ok=False, CFIP_VERSION='v2.1.1')
        self.assertFalse((self.tmpdir / 'escape').exists())
        self.assertFalse((self.root / 'opt').exists())

    def test_archive_symlink_rejected(self):
        directory = self.assets / 'download/v2.1.0'
        for asset in directory.glob('*.tar.gz'):
            with tarfile.open(asset, 'w:gz') as archive:
                member = tarfile.TarInfo('native')
                member.type = tarfile.SYMTYPE
                member.linkname = '/etc'
                archive.addfile(member)
        (directory / 'SHA256SUMS').write_text(''.join(hashlib.sha256(a.read_bytes()).hexdigest() + '  ' + a.name + '\n' for a in directory.glob('*.tar.gz')))
        result = self.run_install(ok=False)
        self.assertIn('Unsafe archive member', result.stderr)

    def test_existing_port_rejected_without_service_stop(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', self.bind_port))
            listener.listen()
            result = self.run_install(ok=False)
        self.assertIn('occupied', result.stderr)
        self.assertNotIn('stop ', (self.root / 'systemctl.calls').read_text())

    def test_failed_update_rolls_back_current_and_service(self):
        self.run_install()
        self.make_release('v2.1.1')
        result = self.run_install('update', ok=False, CFIP_VERSION='v2.1.1', CFIP_TEST_FAIL_START='1')
        self.assertIn('failed', result.stderr)
        self.assertEqual((self.root / 'opt/cfipup2dns/current').resolve().name, 'v2.1.0')
        self.assertTrue((self.root / 'active').exists())

    def test_uninstall_keeps_data_and_legacy(self):
        self.run_install()
        legacy = self.root / 'opt/montecarlo-ip-searcher'
        legacy.mkdir()
        (legacy / 'keep').write_text('legacy')
        self.run_install('uninstall')
        self.assertFalse((self.root / 'opt/cfipup2dns').exists())
        self.assertTrue((self.root / 'var/lib/cfipup2dns/project/config.json').exists())
        self.assertEqual((legacy / 'keep').read_text(), 'legacy')

    def test_explicit_purge(self):
        self.run_install()
        self.run_install('uninstall', '--purge')
        self.assertFalse((self.root / 'var/lib/cfipup2dns').exists())

    def test_unrelated_service_rejected(self):
        path = self.root / 'etc/systemd/system/cfipup2dns.service'
        path.parent.mkdir(parents=True)
        path.write_text('[Service]\nExecStart=/unrelated\n')
        self.run_install(ok=False)
        self.assertIn('/unrelated', path.read_text())

    def test_symlinked_data_rejected(self):
        path = self.root / 'var/lib/cfipup2dns'
        path.parent.mkdir(parents=True)
        path.symlink_to(self.tmpdir / 'assets', target_is_directory=True)
        self.run_install(ok=False)
        self.assertFalse((self.assets / '.install.lock').exists())

    def test_wildcard_listener_rejected(self):
        with socket.socket() as listener:
            listener.bind(('0.0.0.0', self.bind_port))
            listener.listen()
            result = self.run_install(ok=False)
        self.assertIn('occupied', result.stderr)
        self.assertNotIn('stop ', (self.root / 'systemctl.calls').read_text())

    def test_run_uses_saved_settings_and_scan_only_default(self):
        # Execute the real manager + installed entrypoint with a capture-only
        # runner fixture: no scanner/Cloudflare calls are possible.
        (self.source / 'cfip_runner.py').write_text(
            "import json, os; print(json.dumps(dict(os.environ)))\n")
        shutil.rmtree(self.assets / 'download/v2.1.0')
        self.make_release('v2.1.0')
        self.run_install()
        data = self.root / 'var/lib/cfipup2dns'
        (data / 'project/config.json').write_text(json.dumps({
            'run_settings': {'ip_mode': '4', 'top_n': 9},
            'advanced': {'download_mode': 'parallel', 'download_top': '17', 'timeout': '4s'}}))
        cmd = self.root / 'usr/local/bin/cfip'
        result = subprocess.run([str(cmd), 'run'], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        env = json.loads(result.stdout)
        self.assertEqual(env['IP_MODE'], '4')
        self.assertEqual(env['TOP_N'], '9')
        self.assertEqual(env['DOWNLOAD_TOP'], '17')
        self.assertEqual(env['TIMEOUT'], '4s')
        self.assertEqual(env['SCAN_ONLY'], 'true')
        self.assertEqual(env['TZ'], 'Asia/Shanghai')
        result = subprocess.run([str(cmd), 'run', '--sync'], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['SCAN_ONLY'], 'false')

    def test_actual_web_process_health_and_auth(self):
        (self.root / 'health.py').write_text(
            "import json, os, runpy\n"
            "from pathlib import Path\n"
            "root = Path(os.environ['CFIP_INSTALL_ROOT'])\n"
            "for line in (root/'var/lib/cfipup2dns/native.env').read_text().splitlines():\n"
            " key, value = line.split('=', 1); os.environ[key] = json.loads(value)\n"
            "runpy.run_path(str(root/'opt/cfipup2dns/current/web/app.py'), run_name='__main__')\n")
        self.run_install()
        import base64
        import urllib.error
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f'http://127.0.0.1:{self.bind_port}'
        with opener.open(url + '/api/health', timeout=3) as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(urllib.error.HTTPError) as denied:
            opener.open(url + '/api/config', timeout=3)
        self.assertEqual(denied.exception.code, 401)
        values = dict(line.split('=', 1) for line in (self.root/'var/lib/cfipup2dns/native.env').read_text().splitlines())
        auth = base64.b64encode((json.loads(values['WEB_USERNAME']) + ':' + json.loads(values['WEB_PASSWORD'])).encode()).decode()
        request = urllib.request.Request(url + '/api/config', headers={'Authorization': 'Basic ' + auth})
        with opener.open(request, timeout=3) as response:
            self.assertEqual(response.status, 200)

    def test_package_reproducible_and_contains_license(self):
        (self.source / 'bundled-mcis/LICENSE.mcis').write_text('fixture upstream license')
        script = REPO / 'native/package.py'
        # package.py discovers its repository from __file__; use real source
        # through its exported function rather than modifying the repository.
        import importlib.util
        spec = importlib.util.spec_from_file_location('native_package', script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name in ('cfip.sh', 'uninstall.sh'):
            shutil.copy2(REPO / name, self.source / name)
        out = self.tmpdir / 'packaged'
        module.package(self.source, self.source / 'bundled-mcis', 'amd64', out)
        target = out / 'cfipup2dns-linux-amd64.tar.gz'
        first = target.read_bytes()
        module.package(self.source, self.source / 'bundled-mcis', 'amd64', out)
        self.assertEqual(first, target.read_bytes())
        with tarfile.open(target) as archive:
            self.assertIn('bundled-mcis/LICENSE.mcis', archive.getnames())
            self.assertIn('speed_probe.py', archive.getnames())

    def test_failed_first_install_retry_prints_credentials(self):
        self.run_install(ok=False, CFIP_TEST_FAIL_START='1')
        self.assertFalse((self.root / 'var/lib/cfipup2dns/native.env').exists())
        retry = self.run_install()
        self.assertIn('Initial password:', retry.stdout)

    def test_help_without_system_changes(self):
        result = self.run_install('--help')
        self.assertIn('CFIP_VERSION', result.stdout)
        self.assertFalse((self.root / 'opt').exists())


if __name__ == '__main__':
    unittest.main()
