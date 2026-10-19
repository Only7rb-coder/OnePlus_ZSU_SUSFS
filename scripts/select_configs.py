#!/usr/bin/env python3
"""Select device configs for one finite GitHub Actions matrix.

The retry mode deliberately derives its matrix from the completed parent run,
not from a user-supplied list.  This prevents accidentally omitting a failed
kernel or rebuilding a successful one.
"""
import base64
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


LOCK_KEYS = (
    'upstream_build_commit', 'zsu_repository', 'zsu_commit',
    'zsu_manager_release', 'zsu_manager_package', 'manager_certificate_size',
    'manager_certificate_sha256', 'susfs_version', 'susfs_commits',
    'kernel_patches_commit', 'toolchain_mirror_repository',
    'susfs_archive_sha256',
)


def select(root, selector, config_path):
    lock = json.loads((root / 'sources.lock.json').read_text())
    if config_path:
        target = (root / config_path).resolve()
        config_root = (root / 'configs').resolve()
        if not target.is_relative_to(config_root) or target.suffix != '.json' or not target.is_file():
            raise ValueError('config_path must identify an existing configs/**/*.json file')
        paths = [target]
    else:
        paths = sorted((root / 'configs').rglob('*.json'))
    result = []
    for path in paths:
        item = json.loads(path.read_text())
        key = item['android_version'] + '-' + item['kernel_version']
        if not config_path and selector != 'all':
            if selector.startswith('OOS'):
                if item['os_version'] not in selector.split('+'):
                    continue
            elif key != selector:
                continue
        if item.get('susfs') and key not in lock['susfs_commits']:
            raise ValueError(f'No pinned SUSFS revision for {key}')
        item['config_path'] = str(path.relative_to(root))
        item['susfs_commit'] = lock['susfs_commits'].get(key, '')
        result.append(item)
    if not result:
        raise ValueError('Selection matched no device configurations')
    if len(result) > 256:
        raise ValueError('GitHub matrix exceeds 256 jobs; select an OS/kernel group')
    return result


def _github_json(url, token):
    request = Request(
        url,
        headers={
            'Accept': 'application/vnd.github+json',
            'Authorization': f'Bearer {token}',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'zsu-kernel-retry-selector',
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise ValueError(f'GitHub API request failed: {url}: {exc}') from exc


def _paginated(root_url, token, key):
    page = 1
    result = []
    while True:
        query = urlencode({'per_page': 100, 'page': page})
        payload = _github_json(f'{root_url}?{query}', token)
        values = payload.get(key)
        if not isinstance(values, list):
            raise ValueError(f'GitHub API response has no {key} list')
        result.extend(values)
        if len(values) < 100:
            return result
        page += 1


def _parent_source_lock(repository, head_sha, token):
    url = (
        f'https://api.github.com/repos/{quote(repository, safe="/")}'
        f'/contents/sources.lock.json?ref={quote(head_sha, safe="")}'
    )
    payload = _github_json(url, token)
    if payload.get('encoding') != 'base64' or not payload.get('content'):
        raise ValueError('Parent run sources.lock.json was not returned as base64 content')
    try:
        return json.loads(base64.b64decode(payload['content']).decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError('Parent run sources.lock.json is not valid JSON') from exc


def _same_source_identities(current, parent):
    for key in LOCK_KEYS:
        if current.get(key) != parent.get(key):
            raise ValueError(
                f'Parent source lock differs for {key}; refusing a mixed-source retry'
            )


KERNEL_CONCLUSIONS = {'success', 'failure', 'timed_out', 'cancelled', 'skipped'}
RUN_CONCLUSIONS = {'success', 'failure', 'timed_out', 'cancelled'}


def _run_id_list(value):
    values = [part.strip() for part in (value or '').split(',') if part.strip()]
    if len(values) > 8:
        raise ValueError('reuse_runs accepts at most 8 run IDs')
    if len(set(values)) != len(values):
        raise ValueError('reuse_runs contains duplicate run IDs')
    if any(not value.isdigit() for value in values):
        raise ValueError('reuse_runs must contain only numeric GitHub Actions run IDs')
    return values


def _job_records(api_root, run_id, token, by_name, label):
    jobs = _paginated(f'{api_root}/actions/runs/{run_id}/jobs', token, 'jobs')
    records = []
    for job in jobs:
        name = job.get('name')
        if name in {'prepare', 'release'}:
            continue
        if not isinstance(name, str):
            raise ValueError(f'{label} contains a nameless job; refusing partial state')
        pieces = tuple(part.strip() for part in name.split(' / '))
        if len(pieces) != 3 or pieces not in by_name:
            raise ValueError(f'{label} job {name!r} is not a current device configuration')
        if job.get('status') != 'completed':
            raise ValueError(f'{label} job {name!r} is not completed; refusing partial state')
        if any(existing['key'] == pieces for existing in records):
            raise ValueError(f'Duplicate {label.lower()} kernel job {name!r}')
        conclusion = job.get('conclusion')
        if conclusion not in KERNEL_CONCLUSIONS:
            raise ValueError(
                f'{label} job {name!r} has unsupported conclusion {conclusion!r}'
            )
        records.append({'key': pieces, 'config': by_name[pieces], 'conclusion': conclusion})
    if not records:
        raise ValueError(f'{label} has no kernel matrix jobs')
    return records


def _check_run(repository, run_id, token, api_root, label):
    run = _github_json(f'{api_root}/actions/runs/{run_id}', token)
    if run.get('repository', {}).get('full_name') not in (None, repository):
        raise ValueError(f'{label} {run_id} belongs to a different repository')
    if run.get('status') != 'completed':
        raise ValueError(
            f'{label} {run_id} is not completed (status={run.get("status")!r})'
        )
    if run.get('conclusion') not in RUN_CONCLUSIONS:
        raise ValueError(
            f'{label} {run_id} has unsupported conclusion {run.get("conclusion")!r}'
        )
    head_sha = run.get('head_sha')
    if not isinstance(head_sha, str) or len(head_sha) != 40:
        raise ValueError(f'{label} {run_id} has no complete head SHA')
    return run, head_sha


def select_retry(root, repository, run_id, token, reuse_runs=''):
    """Return parent failures not already covered by successful compatible runs."""
    if not run_id or not str(run_id).isdigit():
        raise ValueError('retry_of_run must be a numeric GitHub Actions run ID')
    if not repository or not token:
        raise ValueError('Retry mode requires GITHUB_REPOSITORY and GH_TOKEN')
    reuse_ids = _run_id_list(reuse_runs)
    api_root = f'https://api.github.com/repos/{quote(repository, safe="/")}'
    run, parent_sha = _check_run(repository, str(run_id), token, api_root, 'Parent run')

    root_lock = json.loads((root / 'sources.lock.json').read_text())
    _same_source_identities(root_lock, _parent_source_lock(repository, parent_sha, token))
    all_configs = select(root, 'all', '')
    expected_full = len(all_configs)
    by_name = {
        (item['model'], item['os_version'], item['kernel_version']): item
        for item in all_configs
    }
    parent_jobs = _job_records(api_root, str(run_id), token, by_name, 'Parent run')
    parent_count = len(parent_jobs)
    parent_keys = {entry['key'] for entry in parent_jobs}
    if parent_count == expected_full and expected_full != 158:
        raise ValueError(
            f'Current full matrix has {parent_count} jobs, expected the locked 158'
        )
    if run.get('conclusion') == 'cancelled':
        if expected_full != 158 or parent_count != 158 or parent_keys != set(by_name):
            raise ValueError(
                'Cancelled parent is accepted only with the exact current 158-job matrix'
            )

    target_entries = [entry for entry in parent_jobs if entry['conclusion'] != 'success']
    target_keys = {entry['key'] for entry in target_entries}
    retained = [{
        'run_id': str(run_id), 'success_count': sum(
            entry['conclusion'] == 'success' for entry in parent_jobs
        ), 'source_pipeline_sha': parent_sha,
        'role': 'retained successful parent builds',
    }]
    covered = set()
    for reuse_id in reuse_ids:
        reuse, reuse_sha = _check_run(repository, reuse_id, token, api_root, 'Reuse run')
        _same_source_identities(root_lock, _parent_source_lock(repository, reuse_sha, token))
        reuse_jobs = _job_records(api_root, reuse_id, token, by_name, f'Reuse run {reuse_id}')
        successes = {entry['key'] for entry in reuse_jobs if entry['conclusion'] == 'success'}
        outside = successes - target_keys
        if outside:
            raise ValueError(
                f'Reuse run {reuse_id} has successful jobs outside parent retry targets: '
                f'{sorted(outside)}'
            )
        conflict = covered & successes
        if conflict:
            raise ValueError(f'Reuse runs contain conflicting successful targets: {sorted(conflict)}')
        covered.update(successes)
        retained.append({
            'run_id': str(reuse_id), 'success_count': len(successes),
            'source_pipeline_sha': reuse_sha,
            'role': 'retained successful reusable builds',
        })

    retry = [entry['config'] for entry in target_entries if entry['key'] not in covered]
    release_count = expected_full if parent_count == expected_full else parent_count
    return {
        'configs': retry,
        'parent_count': parent_count,
        'parent_success_count': retained[0]['success_count'],
        'retained_success_count': sum(item['success_count'] for item in retained),
        'retained_runs': retained,
        'parent_run_id': str(run_id),
        'parent_head_sha': parent_sha,
        'current_full_count': expected_full,
        'release_count': release_count,
        'release_only': not retry,
    }


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    retry_of_run = os.environ.get('RETRY_OF_RUN', '').strip()
    if retry_of_run:
        retry = select_retry(
            root,
            os.environ.get('GITHUB_REPOSITORY', ''),
            retry_of_run,
            os.environ.get('GH_TOKEN', ''),
            os.environ.get('REUSE_RUNS', ''),
        )
        matrix = retry['configs']
        count = retry['release_count']
        retry_count = len(matrix)
        parent_run_id = retry['parent_run_id']
        parent_head_sha = retry['parent_head_sha']
        parent_success_count = retry['parent_success_count']
        retained_runs = retry['retained_runs']
        retained_success_count = retry['retained_success_count']
        release_only = str(retry['release_only']).lower()
    else:
        if os.environ.get('REUSE_RUNS', '').strip():
            raise ValueError('reuse_runs requires retry_of_run')
        matrix = select(root, os.environ.get('SELECTOR', 'all'), os.environ.get('CONFIG_PATH', ''))
        count = len(matrix)
        retry_count = 0
        parent_run_id = ''
        parent_head_sha = ''
        parent_success_count = 0
        retained_runs = []
        retained_success_count = 0
        release_only = 'false'
    payload = json.dumps({'include': matrix}, separators=(',', ':'))
    print(f'Selected {len(matrix)} configurations for this run; release count is {count}')
    for config in matrix:
        print(config['config_path'])
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        stream.write(f'matrix={payload}\ncount={count}\nretry_count={retry_count}\n')
        stream.write(f'parent_run_id={parent_run_id}\nparent_head_sha={parent_head_sha}\n')
        stream.write(f'parent_success_count={parent_success_count}\n')
        stream.write(f'retained_success_count={retained_success_count}\n')
        stream.write(f'retained_runs={json.dumps(retained_runs, separators=(",", ":"))}\n')
        stream.write(f'release_only={release_only}\n')
