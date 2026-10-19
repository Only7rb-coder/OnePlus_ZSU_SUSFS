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
        member_names = archive.namelist()
        names = set(member_names)
        if len(member_names) != len(names):
            raise ValueError(f'{path.name} contains duplicate ZIP members')
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


def _run_record(run_id, source_sha, role):
    if not run_id:
        return None
    return {'run_id': str(run_id), 'source_pipeline_sha': source_sha or None, 'role': role}


def main():
    tag = os.environ['RELEASE_TAG']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', tag):
        raise SystemExit('Invalid release tag')
    root = Path(__file__).resolve().parents[1]
    assets = root / 'release-assets'
    zips = sorted(assets.glob('AK3_*.zip'))
    count = int(os.environ['EXPECTED_COUNT'])
    if count < 1:
        raise SystemExit('Expected artifact count must be positive')
    if len(zips) != count:
        raise SystemExit(f'Expected {count} kernel ZIPs, received {len(zips)}; no release will be published')
    hashes = [f'{verify_zip(path)}  {path.name}' for path in zips]
    (assets / 'SHA256SUMS').write_text('\n'.join(hashes) + '\n')
    lock = json.loads((root / 'sources.lock.json').read_text())
    (assets / 'sources.lock.json').write_text(json.dumps(lock, indent=2) + '\n')
    repository = os.environ.get('GITHUB_REPOSITORY', 'Only7rb-coder/OnePlus_ZSU_SUSFS')
    retry_run = os.environ.get('GITHUB_RUN_ID', '')
    retry_sha = os.environ.get('GITHUB_SHA', '')
    parent_run = os.environ.get('PARENT_RUN_ID', '').strip()
    parent_sha = os.environ.get('PARENT_HEAD_SHA', '').strip()
    retry_of_run = os.environ.get('RETRY_OF_RUN', '').strip()
    retry_count = int(os.environ.get('RETRY_COUNT', '0'))
    parent_count = int(os.environ.get('PARENT_COUNT', str(count)))
    retained_json = os.environ.get('RETAINED_RUNS', '').strip()
    if bool(parent_run) != bool(retry_of_run):
        raise SystemExit('Retry provenance requires both PARENT_RUN_ID and RETRY_OF_RUN')
    if retry_of_run and parent_run != retry_of_run:
        raise SystemExit('PARENT_RUN_ID must equal RETRY_OF_RUN')
    if retry_of_run and (retry_count < 0 or parent_count != count):
        raise SystemExit('Retry provenance counts do not match the exact release count')
    records = []
    if parent_run and retained_json:
        try:
            retained = json.loads(retained_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f'Retained run JSON is invalid: {exc}') from exc
        if not isinstance(retained, list) or not retained:
            raise SystemExit('Retained run JSON must be a non-empty list')
        seen = set()
        retained_total = 0
        for item in retained:
            if not isinstance(item, dict):
                raise SystemExit('Retained run records must be objects')
            run_id = str(item.get('run_id', ''))
            success_count = item.get('success_count')
            source_sha = item.get('source_pipeline_sha')
            if (not run_id.isdigit() or run_id in seen or
                    not isinstance(success_count, int) or isinstance(success_count, bool) or
                    success_count < 0 or not isinstance(source_sha, str) or
                    len(source_sha) != 40):
                raise SystemExit('Retained run records have invalid identity or count')
            seen.add(run_id)
            retained_total += success_count
            records.append({
                'run_id': run_id, 'source_pipeline_sha': source_sha,
                'role': item.get('role') or 'retained successful builds',
                'successful_kernel_jobs': success_count,
            })
        if records[0]['run_id'] != parent_run or records[0]['source_pipeline_sha'] != parent_sha:
            raise SystemExit('Retained run list must begin with the retry parent and its head SHA')
        if retained_total + retry_count != count:
            raise SystemExit(
                f'Retained successful jobs ({retained_total}) plus current retry jobs '
                f'({retry_count}) do not equal release count ({count})'
            )
    elif parent_run:
        records.append({
            'run_id': parent_run, 'source_pipeline_sha': parent_sha,
            'role': 'retained successful parent builds',
            'successful_kernel_jobs': int(os.environ.get('PARENT_SUCCESS_COUNT', '0')),
        })
    records.append({
        'run_id': str(retry_run), 'source_pipeline_sha': retry_sha or None,
        'role': 'current build/retry run', 'successful_kernel_jobs': retry_count,
    })
    records = [record for record in records if record]
    lock_digest = hashlib.sha256(
        json.dumps(lock, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    provenance = {
        'schema_version': 1,
        'repository': repository,
        'release_tag': tag,
        'expected_archive_count': count,
        'parent_run_id': parent_run or None,
        'retry_run_id': retry_run or None,
        'retry_of_run': retry_of_run or None,
        'parent_source_pipeline_sha': parent_sha or None,
        'retry_source_pipeline_sha': retry_sha or None,
        'retained_successful_kernel_jobs': sum(
            record.get('successful_kernel_jobs', 0) for record in records[:-1]
        ),
        'recursive_retry_supported': bool(parent_run and len(records) > 2),
        'source_lock_sha256': lock_digest,
        'builds': records,
        'archives': [{'name': path.name, 'sha256': digest.split()[0]}
                     for path, digest in zip(zips, hashes)],
    }
    (assets / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    lines = [
        '# OnePlus ZSU + SUSFS', '',
        (f'Assembled from {len(zips)} newly compiled device/firmware configurations. '
         'These are not renamed BakaSU binaries.' if not parent_run else
         f'Assembled from {parent_count} configurations: {parent_count - retry_count} '
         f'retained successful archives from compatible prior runs and {retry_count} '
         f'archives from run `{retry_run}`. This release does not claim all '
         'archives were built in one workflow run or from one pipeline commit.'), '',
        '**Experimental: compile-validated, not hardware boot-tested.** Match the exact model, OS and full kernel/KMI version before flashing. Back up the original boot image and have a working recovery path. This release does not claim all retained device configurations were tested.', '',
        f'Only the regular `{lock["zsu_manager_package"]}` APK signed by the ZSU certificate is accepted as manager. Generic KernelSU, KernelSU-Next, BakaSU, debug-signed and spoofed-package managers are excluded by the certificate/package policy. Root-authorized apps remain able to use granted root privileges.', '',
        f'Install [ZSU Manager {lock["zsu_manager_release"]}](https://github.com/Only7rb-coder/zsu/releases/tag/{lock["zsu_manager_release"]}). Select the regular `ZSU_*-release.apk`, not the spoofed APK.', '',
        f'ZSU source: `{lock["zsu_commit"]}`. SUSFS: `{lock["susfs_version"]}`. Source lock SHA256: `{lock_digest}`.', '',
        (f'Parent build run: [#{parent_run}](https://github.com/{repository}/actions/runs/{parent_run}) '
         f'(pipeline `{parent_sha}`). Retry build run: '
         f'[#{retry_run}](https://github.com/{repository}/actions/runs/{retry_run}) '
         f'(pipeline `{retry_sha}`).' if parent_run else
         f'Build pipeline commit: `{retry_sha}`. '
         f'[Build logs](https://github.com/{repository}/actions/runs/{retry_run})'), '',
        ('Retry-chain limitation: this workflow does not recursively walk older retry '
         'provenance; use the original completed matrix run as `retry_of_run`.'
         if parent_run else ''),
        'Machine-readable build provenance is attached as `provenance.json`; verify archives with `sha256sum -c SHA256SUMS`.', '',
        '## Kernel downloads', '',
    ]
    lines += [f'- `{path.name}`' for path in zips]
    lines += ['', 'Verify downloads with `sha256sum -c SHA256SUMS`. The attached source lock identifies exact ZSU/SUSFS revisions; this repository contains the ZSU integration patch and the upstream device manifests.', '',
              'Credits: huangdihd, WildKernels, OnePlusOSS, KernelSU-Next, Only7rb-coder/ZSU, simonpunk/SUSFS and all upstream patch authors. Source and license details are in the repository README.']
    (root / 'release-notes.md').write_text('\n'.join(lines) + '\n')
    print(f'Validated {len(zips)} freshly compiled kernel archives')


if __name__ == '__main__':
    main()
