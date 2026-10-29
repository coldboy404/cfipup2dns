#!/usr/bin/env python3
"""Initialize persistent native data without scanning, DNS, or network access."""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile


def atomic_copy(source, target, mode=0o600):
    fd, name = tempfile.mkstemp(prefix='.native-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as out, source.open('rb') as src:
            shutil.copyfileobj(src, out)
        os.chmod(name, mode)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def bootstrap(release, data):
    project = data / 'project'
    for path in (project, data / 'cron', data / 'logs'):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    bundled = release / 'bundled-mcis'
    # Binary/version are managed assets, not user configuration.
    atomic_copy(bundled / 'montecarlo-ip-searcher', project / 'montecarlo-ip-searcher', 0o755)
    atomic_copy(bundled / '.mcis_version', project / '.mcis_version')
    if not (project / 'config.json').exists():
        atomic_copy(release / 'config.example.json', project / 'config.json')
    spec = importlib.util.spec_from_file_location('native_sync_cidr', release / 'docker/sync_cidr.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load bundled CIDR helper')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.sync(project, bundled)
    # Never create cron content: the web scheduler remains opt-in.
