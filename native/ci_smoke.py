#!/usr/bin/env python3
"""Exercise a local release on an ephemeral Ubuntu systemd CI runner only."""
import argparse
import base64
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0:
        raise SystemExit('This destructive smoke test is restricted to root on an ephemeral GitHub Actions runner')
    if Path('/opt/cfipup2dns').exists() or Path('/var/lib/cfipup2dns').exists():
        raise SystemExit('Refusing to replace an existing installation')
    repo = Path(__file__).resolve().parents[1]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix='cfip-release-smoke-') as tmp:
        root = Path(tmp)
        for version in ('v0.0.0-ci1', 'v0.0.0-ci2'):
            destination = root / 'download' / version
            destination.mkdir(parents=True)
            assets = list(args.assets.glob('*.tar.gz'))
            if not assets:
                raise RuntimeError('Missing native archive')
            sums = []
            for asset in assets:
                shutil.copy2(asset, destination / asset.name)
                sums.append(hashlib.sha256(asset.read_bytes()).hexdigest() + '  ' + asset.name)
            (destination / 'SHA256SUMS').write_text('\n'.join(sums) + '\n')
        (root / 'latest').write_text(json.dumps({'tag_name': 'v0.0.0-ci1', 'prerelease': False, 'draft': False}))
        server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(SimpleHTTPRequestHandler, directory=str(root)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = dict(os.environ, CFIP_BASE_URL=f'http://127.0.0.1:{server.server_port}', WEB_BIND_HOST='127.0.0.1', PORT='19527')
        def request(path, auth=None):
            headers = {'Authorization': auth} if auth else {}
            try:
                with opener.open(urllib.request.Request('http://127.0.0.1:19527' + path, headers=headers), timeout=5) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as error:
                return error.code, error.read()
        try:
            subprocess.run(['bash', str(repo / 'install.sh')], env=env, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(['systemctl', 'is-active', '--quiet', 'cfipup2dns.service'], check=True)
            assert request('/api/health')[0] == 200
            assert request('/api/config')[0] == 401
            data = Path('/var/lib/cfipup2dns')
            credentials = (data / 'native.env').read_bytes()
            parsed = {k: json.loads(v) for k, v in (line.split('=', 1) for line in credentials.decode().splitlines())}
            auth = 'Basic ' + base64.b64encode((parsed['WEB_USERNAME'] + ':' + parsed['WEB_PASSWORD']).encode()).decode()
            assert request('/api/config', auth)[0] == 200
            config = data / 'project/config.json'
            config.write_text('{"ci_preserved":true}\n')
            cidr = data / 'project/ipv4cidr.txt'
            cidr.write_text('192.0.2.0/24\n')
            subprocess.run(['bash', str(repo / 'install.sh'), 'update'], env=dict(env, CFIP_VERSION='v0.0.0-ci2'), check=True, stdout=subprocess.DEVNULL)
            assert config.read_text() == '{"ci_preserved":true}\n'
            assert cidr.read_text() == '192.0.2.0/24\n'
            assert (data / 'native.env').read_bytes() == credentials
            assert request('/api/health')[0] == 200
            assert request('/api/config')[0] == 401
            assert Path('/opt/cfipup2dns/current').resolve().name == 'v0.0.0-ci2'
            assert not (data / 'cron/cfip.cron').exists()
            subprocess.run(['/usr/local/bin/cfip', 'uninstall'], env=env, check=True)
            assert config.exists()
            assert not Path('/opt/cfipup2dns').exists()
            print('Native systemd smoke passed: health/auth/update/config/credentials/CIDRs/uninstall preservation')
        finally:
            server.shutdown()
            server.server_close()
            if Path('/usr/local/bin/cfip').is_file():
                subprocess.run(['/usr/local/bin/cfip', 'uninstall'], env=env, check=False)


if __name__ == '__main__':
    main()
