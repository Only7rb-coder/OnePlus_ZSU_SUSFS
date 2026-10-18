#!/usr/bin/env python3
"""Extract only a checksum-pinned upstream SUSFS source archive."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile

root = Path(os.environ.get('GITHUB_WORKSPACE', Path(__file__).resolve().parents[1]))
lock = json.loads((root / 'sources.lock.json').read_text())
commit = sys.argv[1]
if commit not in lock['susfs_commits'].values():
    raise SystemExit('SUSFS commit is not part of the validated source lock')
archive = root / 'vendor/susfs-archives' / f'susfs4ksu-{commit}.tar.gz'
expected = lock['susfs_archive_sha256'].get(commit)
if not expected or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
    raise SystemExit('Pinned SUSFS archive checksum mismatch')
target = Path(os.environ.get('SUSFS_FOLDER', root / 'susfs4ksu'))
if target.exists():
    raise SystemExit('SUSFS extraction target already exists; refusing an implicit overwrite')
with tempfile.TemporaryDirectory() as directory:
    with tarfile.open(archive, 'r:gz') as source:
        source.extractall(directory, filter='data')
    directories = list(Path(directory).iterdir())
    if len(directories) != 1 or not directories[0].is_dir():
        raise SystemExit('Unexpected upstream archive layout')
    shutil.move(str(directories[0]), target)
print(f'Extracted pinned SUSFS {commit}; SHA256={expected}')
