#!/usr/bin/env python3
"""Publish only newly built ARM64 ZIPs; never relabel upstream binaries."""
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile


def verify_zip(path):
    with zipfile.ZipFile(path) as archive:
        damaged = archive.testzip()
        if damaged:
            raise ValueError(f'ZIP CRC failure: {damaged}')
        names = set(archive.namelist())
        if 'Image' not in names or 'anykernel.sh' not in names:
            raise ValueError(f'{path.name} lacks Image / AnyKernel3 installer')
        image = archive.read('Image')
        if len(image) < 6102400 or image[56:60] != b'ARM\x64':
            raise ValueError(f'{path.name} is not a valid-size ARM64 Image')
        if 'ZSU' not in path.name:
            raise ValueError('Artifact does not identify the ZSU variant')
        for name in names:
            if name.endswith(('.keystore', '.jks', '.pem')) or '/.git/' in name:
                raise ValueError(f'Unexpected signing key or repository metadata in archive: {name}')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    tag = os.environ['RELEASE_TAG']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', tag):
        raise SystemExit('Invalid release tag')
    root = Path(__file__).resolve().parents[1]
    assets = root / 'release-assets'
    zips = sorted(assets.glob('AK3_*.zip'))
    count = int(os.environ['EXPECTED_COUNT'])
    if len(zips) != count:
        raise SystemExit(f'Expected {count} kernel ZIPs, received {len(zips)}; no release will be published')
    hashes = [f'{verify_zip(path)}  {path.name}' for path in zips]
    (assets / 'SHA256SUMS').write_text('\n'.join(hashes) + '\n')
    lock = json.loads((root / 'sources.lock.json').read_text())
    (assets / 'sources.lock.json').write_text(json.dumps(lock, indent=2) + '\n')
    repository = os.environ.get('GITHUB_REPOSITORY', 'Only7rb-coder/OnePlus_ZSU_SUSFS')
    run = os.environ.get('GITHUB_RUN_ID', '')
    commit = os.environ.get('GITHUB_SHA', '')
    lines = [
        '# OnePlus ZSU + SUSFS', '',
        f'Freshly compiled from source: {len(zips)} device/firmware configurations. These are not renamed BakaSU binaries.', '',
        '**Experimental: compile-validated, not hardware boot-tested.** Match the exact model, OS and full kernel/KMI version before flashing. Back up the original boot image and have a working recovery path. This release does not claim all retained device configurations were tested.', '',
        f'Only the regular `{lock["zsu_manager_package"]}` APK signed by the ZSU certificate is accepted as manager. Generic KernelSU, KernelSU-Next, BakaSU, debug-signed and spoofed-package managers are excluded by the certificate/package policy. Root-authorized apps remain able to use granted root privileges.', '',
        f'Install [ZSU Manager {lock["zsu_manager_release"]}](https://github.com/Only7rb-coder/zsu/releases/tag/{lock["zsu_manager_release"]}). Select the regular `ZSU_*-release.apk`, not the spoofed APK.', '',
        f'ZSU source: `{lock["zsu_commit"]}`. SUSFS: `{lock["susfs_version"]}`. Build pipeline commit: `{commit}`.', '',
        f'[Build logs](https://github.com/{repository}/actions/runs/{run})', '',
        '## Kernel downloads', '',
    ]
    lines += [f'- `{path.name}`' for path in zips]
    lines += ['', 'Verify downloads with `sha256sum -c SHA256SUMS`. The attached source lock identifies exact ZSU/SUSFS revisions; this repository contains the ZSU integration patch and the upstream device manifests.', '',
              'Credits: huangdihd, WildKernels, OnePlusOSS, KernelSU-Next, Only7rb-coder/ZSU, simonpunk/SUSFS and all upstream patch authors. Source and license details are in the repository README.']
    (root / 'release-notes.md').write_text('\n'.join(lines) + '\n')
    print(f'Validated {len(zips)} freshly compiled kernel archives')


if __name__ == '__main__':
    main()
