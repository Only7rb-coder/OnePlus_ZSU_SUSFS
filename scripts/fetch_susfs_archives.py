#!/usr/bin/env python3
"""Maintainer utility: mirror locked public SUSFS sources, including upstream license."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
lockfile = root / 'sources.lock.json'
lock = json.loads(lockfile.read_text())
folder = root / 'vendor/susfs-archives'
folder.mkdir(parents=True, exist_ok=True)


def fetch(commit):
    path = folder / f'susfs4ksu-{commit}.tar.gz'
    if not path.exists():
        url = ('https://gitlab.com/api/v4/projects/simonpunk%2Fsusfs4ksu/repository/archive.tar.gz'
               f'?sha={commit}&include_lfs_blobs=false')
        temporary = path.with_suffix('.part')
        subprocess.run(['curl', '-fL', '--retry', '3', '--connect-timeout', '30', '--max-time', '180',
                        url, '-o', str(temporary)], check=True)
        temporary.replace(path)
    with tarfile.open(path, 'r:gz') as archive:
        names = archive.getnames()
        if not any(name.endswith('/LICENSE') for name in names):
            raise RuntimeError('Upstream license missing from archive')
        header = next(name for name in names if name.endswith('/kernel_patches/include/linux/susfs.h'))
        if f'#define SUSFS_VERSION "{lock["susfs_version"]}"'.encode() not in archive.extractfile(header).read():
            raise RuntimeError(f'SUSFS ABI version mismatch for {commit}')
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    print(commit, checksum, flush=True)
    return commit, checksum


with ThreadPoolExecutor(max_workers=3) as executor:
    checksums = dict(executor.map(fetch, sorted(set(lock['susfs_commits'].values()))))
lock['susfs_archive_sha256'] = checksums
lockfile.write_text(json.dumps(lock, indent=2) + '\n')
