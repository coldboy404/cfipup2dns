#!/usr/bin/env python3
"""Create deterministic native release archives from app + pinned upstream output."""
import argparse
import gzip
from pathlib import Path
import shutil
import tarfile
import tempfile


def package(repo, bundled, arch, output):
    required = ('.mcis_version', 'montecarlo-ip-searcher', 'ipv4cidr.txt', 'ipv6cidr.txt', 'LICENSE.mcis')
    if not all((bundled / name).is_file() for name in required):
        raise ValueError('Incomplete bundled mcis output')
    marker = (bundled / '.mcis_version').read_text().strip()
    if marker != 'add8ab94463ec440d17d354c858be7346a52b95e+source':
        raise ValueError('Bundled upstream version marker does not match pinned source')
    output.mkdir(parents=True, exist_ok=True)
    target = output / f'cfipup2dns-linux-{arch}.tar.gz'
    with tempfile.TemporaryDirectory(prefix='cfip-package-') as tmp:
        stage = Path(tmp)
        for name in ('install.sh', 'uninstall.sh', 'cfip.sh', 'cfip_runner.py', 'speed_probe.py', 'config.example.json'):
            shutil.copy2(repo / name, stage / name)
        for name in ('LICENSE', 'README.md'):
            if (repo / name).is_file():
                shutil.copy2(repo / name, stage / name)
        for name in ('web', 'native'):
            shutil.copytree(repo / name, stage / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (stage / 'docker').mkdir()
        shutil.copy2(repo / 'docker/sync_cidr.py', stage / 'docker/sync_cidr.py')
        (stage / 'bundled-mcis').mkdir()
        for name in required:
            shutil.copy2(bundled / name, stage / 'bundled-mcis' / name)
        with target.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for path in sorted(stage.rglob('*')):
                    if not path.is_file():
                        continue
                    info = archive.gettarinfo(str(path), arcname=str(path.relative_to(stage)))
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ''
                    info.mode = 0o755 if path.name == 'montecarlo-ip-searcher' or path.suffix == '.sh' else 0o644
                    with path.open('rb') as stream:
                        archive.addfile(info, stream)
    print(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundled', type=Path, required=True)
    parser.add_argument('--arch', choices=('amd64', 'arm64'), required=True)
    parser.add_argument('--output', type=Path, default=Path('dist'))
    args = parser.parse_args()
    package(Path(__file__).resolve().parents[1], args.bundled, args.arch, args.output)
