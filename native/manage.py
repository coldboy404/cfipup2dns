#!/usr/bin/env python3
"""Native deployment manager. No shell-sourcing, package installs, cron, or DNS."""
import argparse
import fcntl
import ipaddress
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request

from bootstrap import atomic_copy, bootstrap

SERVICE = 'cfipup2dns.service'
MARKER = '# cfipup2dns native installer v1'
ROOT = os.environ.get('CFIP_INSTALL_ROOT', '')
if ROOT and (not Path(ROOT).is_absolute() or Path(ROOT).resolve() == Path('/') or re.search(r'[\s"\\%]', ROOT)):
    raise SystemExit('Unsafe CFIP_INSTALL_ROOT')
BASE = Path(ROOT + '/opt/cfipup2dns')
DATA = Path(ROOT + '/var/lib/cfipup2dns')
UNIT = Path(ROOT + '/etc/systemd/system/' + SERVICE)
COMMAND = Path(ROOT + '/usr/local/bin/cfip')
RUN = Path(ROOT + '/usr/local/bin/cfip-run')
ENV = DATA / 'native.env'
OWNER = BASE / '.native-install'


def systemctl(*args, check=True):
    return subprocess.run(['systemctl', *args], check=check, timeout=45,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def write(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.cfip-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(text)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_env():
    info = ENV.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError('native.env must be a regular file owned by installer user, mode 0600')
    result = {}
    for line in ENV.read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        key, value = line.split('=', 1)
        if not re.fullmatch('[A-Z_][A-Z0-9_]*', key):
            raise RuntimeError('Invalid environment key')
        value = json.loads(value)
        if not isinstance(value, str) or '\n' in value or '\r' in value or '\x00' in value:
            raise RuntimeError('Invalid environment value')
        result[key] = value
    return result


def defaults():
    host = os.environ.get('WEB_BIND_HOST', '127.0.0.1')
    # Literal IP only: health and collision checks must address the exact bind.
    ipaddress.ip_address(host)
    if ':' in host:
        raise RuntimeError('The stdlib web server currently supports IPv4 binds only')
    port = int(os.environ.get('PORT', os.environ.get('WEB_BIND_PORT', '9527')))
    if not 1 <= port <= 65535:
        raise RuntimeError('PORT must be 1..65535')
    return {
        'PROJECT_DIR': str(DATA / 'project'), 'CONFIG_FILE': str(DATA / 'project/config.json'),
        'CRON_FILE': str(DATA / 'cron/cfip.cron'), 'LOG_FILE': str(DATA / 'logs/cron.log'),
        'INIT_LOG_FILE': str(DATA / 'logs/init-mcis.log'), 'BOOT_LOG_FILE': str(DATA / 'logs/boot.log'),
        'RUN_CMD': str(RUN), 'WEB_BIND_HOST': host, 'PORT': str(port),
        'WEB_USERNAME': 'admin', 'WEB_PASSWORD': secrets.token_hex(24),
        'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'TZ': 'Asia/Shanghai',
    }


def owned():
    return OWNER.is_file() and OWNER.read_text().strip() == MARKER


def unit_owned():
    return UNIT.is_file() and UNIT.read_text().startswith(MARKER + '\n')


def preflight():
    if not ROOT and (os.geteuid() != 0 or not Path('/run/systemd/system').is_dir()):
        raise RuntimeError('Root and running systemd required')
    # Refuse path aliases before any write/delete, even in the sandbox.
    for target in (BASE, BASE / 'releases', DATA, DATA / 'project', DATA / 'cron', DATA / 'logs', ENV,
                   DATA / 'project/config.json', DATA / 'project/ipv4cidr.txt', DATA / 'project/ipv6cidr.txt',
                   DATA / 'project/.cidr-manifest.json', UNIT, COMMAND, RUN):
        for path in (target, *target.parents):
            if path.is_symlink() and path != BASE / 'current':
                raise RuntimeError('Refusing symlinked installation path: ' + str(path))
    if BASE.exists() and not owned():
        raise RuntimeError('Existing /opt/cfipup2dns is not a managed native install; refusing to modify it')
    if UNIT.exists() and not unit_owned():
        raise RuntimeError('Existing unrelated service; refusing to modify it')
    fragment = systemctl('show', SERVICE, '--property=FragmentPath', '--value', check=False)
    existing_unit = fragment.stdout.strip()
    if existing_unit and existing_unit != str(UNIT):
        raise RuntimeError('An unrelated systemd service already uses ' + SERVICE)
    for path in (COMMAND, RUN):
        if path.exists() and (not owned() or MARKER not in path.read_text()):
            raise RuntimeError('Existing command is not managed; refusing to replace ' + str(path))


def port_available(env):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((env['WEB_BIND_HOST'], int(env['PORT'])))
        except OSError as error:
            raise RuntimeError('Bind/port is occupied (possibly Docker); existing service left untouched') from error


def switch(target):
    link = BASE / '.current-next'
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target)
    os.replace(link, BASE / 'current')


def health(env):
    host = env['WEB_BIND_HOST']
    if host == '0.0.0.0':
        host = '127.0.0.1'
    url = 'http://%s:%s/api/health' % (host, env['PORT'])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(20):
        if systemctl('is-active', '--quiet', SERVICE, check=False).returncode == 0:
            try:
                with opener.open(url, timeout=2) as response:
                    if response.status == 200 and json.load(response).get('ok') is True:
                        return
            except (OSError, ValueError):
                pass
        time.sleep(0.5)
    raise RuntimeError('Service failed health at ' + url)


def install(source, version):
    if not re.fullmatch(r'v[0-9][A-Za-z0-9._+-]{0,99}', version) or '..' in version:
        raise RuntimeError('Invalid release version')
    preflight()
    first = not ENV.exists()
    env = defaults() if first else load_env()
    previous = (BASE / 'current').resolve() if (BASE / 'current').is_symlink() else None
    if (BASE / 'current').exists() and previous is None:
        raise RuntimeError('current must be a managed release symlink')
    if previous and (previous.parent != BASE / 'releases' or not previous.is_dir()):
        raise RuntimeError('Unsafe previous release target')
    active = owned() and unit_owned() and systemctl('is-active', '--quiet', SERVICE, check=False).returncode == 0
    if not active:
        port_available(env)
    # Existing current-version downloads are immutable: rerun heals startup only.
    release = BASE / 'releases' / version
    if release.exists() and (not release.is_dir() or release.is_symlink()):
        raise RuntimeError('Unsafe release path')
    BASE.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    DATA.chmod(0o700)
    (BASE / 'releases').mkdir(exist_ok=True)
    if not release.exists():
        staging = Path(tempfile.mkdtemp(prefix='.release-', dir=BASE / 'releases'))
        try:
            shutil.copytree(source, staging, dirs_exist_ok=True)
            os.replace(staging, release)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    old_unit = UNIT.read_text() if UNIT.exists() else None
    old_commands = {p: p.read_text() if p.exists() else None for p in (COMMAND, RUN)}
    was_enabled = systemctl('is-enabled', '--quiet', SERVICE, check=False).returncode == 0
    try:
        # Stop only our unit, and only after integrity/preflight passed.
        if active:
            systemctl('stop', SERVICE)
        bootstrap(release, DATA)
        if first:
            write(ENV, ''.join(key + '=' + json.dumps(value) + '\n' for key, value in env.items()))
        switch(release)
        write(OWNER, MARKER + '\n')
        python = shutil.which('python3')
        if python is None:
            raise RuntimeError('python3 is required')
        write(COMMAND, '#!/bin/sh\n' + MARKER + '\nexec ' + python + ' ' + str(BASE / 'current/native/manage.py') + ' "$@"\n', 0o755)
        # Web invokes this wrapper with its own per-run env; do not reload the
        # environment here (that would discard its SCAN_ONLY/run settings).
        write(RUN, '#!/bin/sh\n' + MARKER + '\nexec ' + python + ' ' + str(BASE / 'current/cfip_runner.py') + ' "$@"\n', 0o755)
        write(UNIT, MARKER + '\n' + f'''[Unit]
Description=cfipup2dns native WebUI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile={ENV}
WorkingDirectory={DATA / 'project'}
ExecStart={python} {BASE / 'current/web/app.py'}
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
''', 0o644)
        systemctl('daemon-reload')
        systemctl('enable', SERVICE)
        systemctl('restart', SERVICE)
        health(env)
    except Exception:
        systemctl('stop', SERVICE, check=False)
        if previous:
            switch(previous)
            bootstrap(previous, DATA)
        elif (BASE / 'current').is_symlink():
            (BASE / 'current').unlink()
        if old_unit is None:
            if first:
                ENV.unlink(missing_ok=True)
            UNIT.unlink(missing_ok=True)
        else:
            write(UNIT, old_unit, 0o644)
        for path, text in old_commands.items():
            if text is None:
                path.unlink(missing_ok=True)
            else:
                write(path, text, 0o755)
        systemctl('daemon-reload', check=False)
        if not was_enabled:
            systemctl('disable', SERVICE, check=False)
        if active and previous:
            systemctl('start', SERVICE, check=False)
        raise
    print('Native release ' + version + ' healthy at http://' + env['WEB_BIND_HOST'] + ':' + env['PORT'])
    if first:
        print('Initial username: ' + env['WEB_USERNAME'])
        print('Initial password: ' + env['WEB_PASSWORD'])
        print('Credentials saved in ' + str(ENV) + ' (0600); never printed again.')
    print('No scan/DNS/cron job started. Configure WebUI before opting into runs/scheduling.')


def uninstall(purge=False):
    preflight()
    if not owned():
        raise RuntimeError('No managed native installation; nothing removed')
    if not unit_owned():
        raise RuntimeError('Managed unit missing or changed; refusing removal')
    systemctl('disable', '--now', SERVICE)
    UNIT.unlink()
    systemctl('daemon-reload')
    for path in (COMMAND, RUN):
        if path.is_file() and MARKER in path.read_text():
            path.unlink()
    # Exact owned native directory only. Never touches legacy /opt/montecarlo*,
    # Docker, global Go, crontabs, or any other service/process.
    shutil.rmtree(BASE)
    if purge and DATA.exists():
        shutil.rmtree(DATA)
    print('Native installation removed; data ' + ('purged' if purge else 'preserved at ' + str(DATA)))


def run_environment():
    """Use the web's validated saved settings; CLI env overrides are not applied."""
    env = dict(os.environ, **load_env())
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location('native_web_settings', BASE / 'current/web/app.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load installed web settings')
    web = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web)  # __main__ guard: no HTTP server/scheduler spawned.
    settings = web.run_settings_state(web.load_config())
    env['IP_MODE'] = str(settings['ip_mode'])
    env['TOP_N'] = str(settings['top_n'])
    return web.apply_advanced_env(env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'update', 'uninstall', 'status', 'logs', 'start', 'stop', 'restart', 'run'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--version')
    parser.add_argument('--purge', action='store_true')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--scan-only', action='store_true')
    mode.add_argument('--sync', action='store_true', help='Explicitly allow scan-to-DNS changes')
    args = parser.parse_args()
    if args.purge and args.action != 'uninstall':
        parser.error('--purge is for uninstall only')
    if (args.scan_only or args.sync) and args.action != 'run':
        parser.error('run flags are for run only')
    if args.action in ('install', 'update') and args.source:
        if not args.version:
            parser.error('--version is required with --source')
        # One lock outside release tree, retained with data.
        preflight()
        DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (DATA / '.install.lock').is_symlink():
            raise RuntimeError('Refusing symlinked install lock')
        with (DATA / '.install.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            install(args.source, args.version)
    elif args.action == 'update':
        os.execv('/bin/sh', ['/bin/sh', str(BASE / 'current/install.sh'), 'update'])
    elif args.action == 'uninstall':
        uninstall(args.purge)
    elif args.action == 'run':
        env = run_environment()
        env['SCAN_ONLY'] = 'false' if args.sync else 'true'
        python = shutil.which('python3')
        if python is None:
            raise RuntimeError('python3 is required')
        os.execve(python, ['python3', str(BASE / 'current/cfip_runner.py')], env)
    elif args.action == 'logs':
        log = Path(load_env()['LOG_FILE'])
        if log.is_file():
            print('Task log: ' + str(log) + '; service diagnostics: journalctl -u ' + SERVICE, flush=True)
            os.execvp('tail', ['tail', '-n', '100', '-F', str(log)])
        os.execvp('journalctl', ['journalctl', '-u', SERVICE, '-n', '100', '-f'])
    else:
        os.execvp('systemctl', ['systemctl', args.action, SERVICE])


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Native deployment failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
