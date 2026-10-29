#!/bin/sh
# Native installer: stdlib Python, prebuilt assets, no Docker/Go/package changes.
set -eu
case "${1:-install}" in
  -h|--help|help)
    cat <<'HELP'
Usage: bash install.sh [install|update|uninstall [--purge]]
Requires Linux amd64/arm64, root, systemd, Python >=3.9, curl, tar, sha256sum.
Default: latest stable GitHub release, 127.0.0.1:9527, admin/random password.
CFIP_VERSION=vX.Y.Z pins a release. WEB_BIND_HOST and PORT set first-install bind.
WEB_BIND_PORT is also accepted (PORT takes precedence).
CFIP_BASE_URL overrides the releases root (latest JSON at BASE/latest;
assets at BASE/download/TAG/NAME). CFIP_INSTALL_ROOT is a sandbox-only prefix.
Updates preserve credentials/config/CIDRs; uninstall keeps data unless --purge.
Management: cfip status|logs|start|stop|restart|update|uninstall|run [--scan-only|--sync]
cfip run uses saved WebUI run/advanced settings; default scan-only, --sync allows DNS.
cfip logs tails task log when present; otherwise follows service journal.
HELP
    exit 0 ;;
  install|update|uninstall) ;;
  *) printf '%s\n' 'Unknown action; use --help' >&2; exit 2 ;;
esac
for cmd in python3 curl tar sha256sum systemctl; do
  command -v "$cmd" >/dev/null 2>&1 || { printf 'Missing prerequisite: %s\n' "$cmd" >&2; exit 1; }
done
python3 - "$@" <<'PY'
import hashlib, json, os, platform, re, shutil, subprocess, sys, tarfile, tempfile
from pathlib import Path, PurePosixPath

if sys.version_info < (3, 9):
    raise SystemExit('Python >=3.9 required')
root = os.environ.get('CFIP_INSTALL_ROOT', '')
if root and (not Path(root).is_absolute() or Path(root).resolve() == Path('/')):
    raise SystemExit('CFIP_INSTALL_ROOT must be an absolute sandbox directory, not /')
if platform.system() != 'Linux' or (not root and os.geteuid() != 0):
    raise SystemExit('Linux and root required (except sandbox tests)')
if not root and not Path('/run/systemd/system').is_dir():
    raise SystemExit('A running systemd host is required')
action = sys.argv[1] if len(sys.argv) > 1 else 'install'
if action == 'uninstall':
    manager = Path(root + '/opt/cfipup2dns/current/native/manage.py')
    if not manager.is_file():
        raise SystemExit('Native installation not found; nothing removed')
    raise SystemExit(subprocess.call([sys.executable, str(manager), *sys.argv[1:]]))
if len(sys.argv) > 2:
    raise SystemExit('Unexpected arguments; use --help')
arch = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine())
if not arch:
    raise SystemExit('Supported architectures: amd64, arm64')
base = os.environ.get('CFIP_BASE_URL', 'https://github.com/coldboy404/cfipup2dns/releases').rstrip('/')
def fetch(url, dest):
    subprocess.run(['curl', '--fail', '--silent', '--show-error', '--location',
                    '--connect-timeout', '10', '--max-time', '180', '--retry', '1',
                    '--proto', '=https,http', url, '-o', str(dest)], check=True, timeout=380)
# Temporary files live below the caller's TMPDIR if configured.
with tempfile.TemporaryDirectory(prefix='cfip-native-') as tmp:
    tmp = Path(tmp)
    version = os.environ.get('CFIP_VERSION', '')
    if not version:
        latest = base + '/latest' if 'CFIP_BASE_URL' in os.environ else 'https://api.github.com/repos/coldboy404/cfipup2dns/releases/latest'
        fetch(latest, tmp / 'latest.json')
        info = json.loads((tmp / 'latest.json').read_text())
        if info.get('draft') or info.get('prerelease'):
            raise SystemExit('Expected a stable published release')
        version = info['tag_name']
    if not re.fullmatch(r'v[0-9][A-Za-z0-9._+-]{0,99}', version) or '..' in version:
        raise SystemExit('Invalid CFIP_VERSION release tag')
    asset = 'cfipup2dns-linux-' + arch + '.tar.gz'
    fetch(base + '/download/' + version + '/SHA256SUMS', tmp / 'SHA256SUMS')
    sums = []
    for line in (tmp / 'SHA256SUMS').read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip('*') == asset:
            sums.append(parts[0])
    if len(sums) != 1 or not re.fullmatch('[0-9a-fA-F]{64}', sums[0]):
        raise SystemExit('Missing or ambiguous asset checksum')
    fetch(base + '/download/' + version + '/' + asset, tmp / asset)
    digest = hashlib.sha256()
    with (tmp / asset).open('rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            digest.update(chunk)
    if digest.hexdigest() != sums[0].lower():
        raise SystemExit('SHA256 mismatch; installation unchanged')
    source = tmp / 'source'
    source.mkdir()
    with tarfile.open(tmp / asset, 'r:gz') as archive:
        members = archive.getmembers()
        total = 0
        seen = set()
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or '\\' in member.name or not (member.isdir() or member.isfile()):
                raise SystemExit('Unsafe archive member: ' + member.name)
            if str(name) in seen:
                raise SystemExit('Duplicate archive member')
            seen.add(str(name))
            total += member.size
            if total > 1024 * 1024 * 1024 or len(members) > 10000:
                raise SystemExit('Archive exceeds safety limits')
        # All entries validated before writing; links/devices/FIFOs are forbidden.
        for member in members:
            destination = source.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as src, destination.open('wb') as out:
                    shutil.copyfileobj(src, out)
                destination.chmod(0o755 if member.mode & 0o111 else 0o644)
    required = ['native/manage.py', 'native/bootstrap.py', 'cfip_runner.py', 'speed_probe.py', 'web/app.py', 'config.example.json',
                'docker/sync_cidr.py', 'install.sh', 'bundled-mcis/.mcis_version', 'bundled-mcis/montecarlo-ip-searcher',
                'bundled-mcis/ipv4cidr.txt', 'bundled-mcis/ipv6cidr.txt']
    if not all((source / p).is_file() for p in required):
        raise SystemExit('Incomplete native release')
    subprocess.run([sys.executable, str(source / 'native/manage.py'), action, '--source', str(source), '--version', version], check=True)
PY
