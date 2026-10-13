#!/usr/bin/env python3
"""Update only unmodified managed CIDR lists; preserve user-owned lists."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import tempfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    fd, name = tempfile.mkstemp(prefix='.cidr-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def sync(project, bundled):
    project.mkdir(parents=True, exist_ok=True)
    marker = project / '.cidr-manifest.json'
    try:
        previous = json.loads(marker.read_text())
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, ValueError):
        previous = {}
    manifest = {}
    for version in (4, 6):
        name = f'ipv{version}cidr.txt'
        source = (bundled / name).read_bytes()
        networks = [ipaddress.ip_network(line.strip(), strict=False) for line in source.decode().splitlines() if line.strip() and not line.lstrip().startswith('#')]
        if not networks or any(net.version != version for net in networks):
            raise ValueError(f'Invalid bundled CIDR list: {name}')
        target = project / name
        existing = target.read_bytes() if target.exists() else b''
        old = previous.get(name, {})
        managed = isinstance(old, dict) and old.get('managed') is True and old.get('sha256') == digest(existing)
        if not existing or managed or existing == source:
            if existing != source:
                atomic_write(target, source)
            manifest[name] = {'managed': True, 'sha256': digest(source), 'entries': len(networks)}
            print(f'[CIDR] {name}: 内置库，{len(networks)} 条', flush=True)
        else:
            manifest[name] = {'managed': False, 'sha256': digest(existing)}
            print(f'[CIDR] {name}: 保留自定义/旧版未托管库；删除该文件后重启可恢复内置库', flush=True)
    atomic_write(marker, json.dumps(manifest, indent=2).encode())
    return manifest


if __name__ == '__main__':
    sync(Path(os.getenv('PROJECT_DIR', '/data/project')), Path('/opt/cfipup2dns/bundled-mcis'))
