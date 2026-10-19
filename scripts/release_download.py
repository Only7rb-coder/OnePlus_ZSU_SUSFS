#!/usr/bin/env python3
"""Download retained AK3 archives from a completed GitHub Actions run.

GitHub artifact downloads are ZIP containers, so this helper extracts only the
single expected AK3_*.zip file from each matching artifact and rejects path
traversal, duplicates, expired artifacts, and count mismatches.  It also
flattens current-run artifacts into the same directory with intentional
replacement so a retried archive wins over a retained archive of the same name.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
import zipfile


class DownloadError(ValueError):
    """A release input is unsafe or incomplete."""


def github_json(url, token):
    request = Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'zsu-kernel-release-downloader',
    })
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise DownloadError(f'GitHub API request failed: {url}: {exc}') from exc


def github_bytes(url, token):
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            return None

    request = Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'zsu-kernel-release-downloader',
    })
    try:
        try:
            with build_opener(NoRedirect()).open(request, timeout=180) as response:
                return response.read()
        except HTTPError as redirect:
            if redirect.code not in (301, 302, 303, 307, 308):
                raise
            location = redirect.headers.get('Location', '')
            if urlparse(location).scheme != 'https':
                raise DownloadError('Artifact API returned a non-HTTPS redirect')
            # The signed storage URL authenticates itself. Forwarding GH_TOKEN
            # breaks Azure downloads and needlessly exposes it to the CDN.
            with urlopen(Request(location, headers={'User-Agent': 'zsu-kernel-release-downloader'}), timeout=180) as response:
                return response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        status = getattr(exc, 'code', type(exc).__name__)
        raise DownloadError(f'GitHub artifact download failed: {url}; status={status}') from None


def list_artifacts(repository, run_id, token):
    root = f'https://api.github.com/repos/{quote(repository, safe="/")}'
    artifacts = []
    page = 1
    while True:
        query = urlencode({'per_page': 100, 'page': page})
        payload = github_json(f'{root}/actions/runs/{run_id}/artifacts?{query}', token)
        page_items = payload.get('artifacts')
        if not isinstance(page_items, list):
            raise DownloadError('GitHub artifact response is malformed')
        artifacts.extend(page_items)
        if len(page_items) < 100:
            return artifacts
        page += 1


def _archive_member_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or len(path.parts) != 1:
        raise DownloadError(f'Unsafe member path in GitHub artifact: {name!r}')
    if not path.name.startswith('AK3_') or not path.name.endswith('.zip'):
        return None
    return path.name


def download_parent(repository, run_id, token, destination, expected_count):
    if not str(run_id).isdigit():
        raise DownloadError('Parent run ID must be numeric')
    if expected_count < 0:
        raise DownloadError('Expected retained artifact count cannot be negative')
    destination.mkdir(parents=True, exist_ok=True)
    artifacts = [item for item in list_artifacts(repository, run_id, token)
                 if isinstance(item.get('name'), str)
                 and item['name'].startswith('AK3_')
                 and item['name'].endswith('.zip')]
    if len(artifacts) != expected_count:
        raise DownloadError(
            f'Parent run exposes {len(artifacts)} AK3 artifacts, expected {expected_count}'
        )
    seen = set()
    root = f'https://api.github.com/repos/{quote(repository, safe="/")}'
    for artifact in artifacts:
        if artifact.get('expired'):
            raise DownloadError(f'Parent artifact {artifact.get("name")} is expired')
        artifact_name = artifact['name']
        if artifact_name in seen:
            raise DownloadError(f'Duplicate parent artifact name: {artifact_name}')
        seen.add(artifact_name)
        payload = github_bytes(
            f'{root}/actions/artifacts/{artifact["id"]}/zip', token
        )
        try:
            with zipfile.ZipFile(__import__('io').BytesIO(payload)) as container:
                members = [_archive_member_name(name) for name in container.namelist()]
                members = [name for name in members if name is not None]
                if members != [artifact_name]:
                    raise DownloadError(
                        f'Artifact {artifact_name} does not contain exactly itself'
                    )
                target = destination / artifact_name
                target.write_bytes(container.read(artifact_name))
        except zipfile.BadZipFile as exc:
            raise DownloadError(f'Artifact {artifact_name} is not a ZIP container') from exc
    return len(seen)


def download_runs(repository, runs, token, destination):
    """Download retained runs oldest-first; later compatible runs replace names."""
    if not isinstance(runs, list) or not runs:
        raise DownloadError('retained run list must be a non-empty JSON array')
    seen_runs = set()
    total = 0
    for record in runs:
        if not isinstance(record, dict):
            raise DownloadError('retained run record must be an object')
        run_id = str(record.get('run_id', ''))
        if not run_id.isdigit() or run_id in seen_runs:
            raise DownloadError(f'Invalid or duplicate retained run ID: {run_id!r}')
        expected = record.get('success_count')
        sha = record.get('source_pipeline_sha')
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            raise DownloadError(f'Invalid successful-job count for retained run {run_id}')
        if not isinstance(sha, str) or len(sha) != 40:
            raise DownloadError(f'Invalid source pipeline SHA for retained run {run_id}')
        seen_runs.add(run_id)
        total += download_parent(repository, run_id, token, destination, expected)
    return total


def flatten_local(source, destination, expected_count):
    if not source.is_dir():
        raise DownloadError(f'Current-run artifact directory does not exist: {source}')
    files = sorted(path for path in source.rglob('AK3_*.zip') if path.is_file())
    if len(files) != expected_count:
        raise DownloadError(
            f'Current run exposes {len(files)} AK3 artifacts, expected {expected_count}'
        )
    destination.mkdir(parents=True, exist_ok=True)
    names = set()
    for path in files:
        name = path.name
        if _archive_member_name(name) != name:
            raise DownloadError(f'Unsafe current-run artifact name: {name!r}')
        if name in names:
            raise DownloadError(f'Duplicate current-run artifact name: {name}')
        names.add(name)
        shutil.copy2(path, destination / name)
    return len(names)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', help='Completed parent GitHub Actions run ID')
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY', ''))
    parser.add_argument('--token', default=os.environ.get('GH_TOKEN', ''))
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--expected-count', type=int, required=True)
    parser.add_argument('--runs-json', help='JSON retained run list from prepare outputs')
    parser.add_argument('--flatten-from', type=Path,
                        help='Flatten current-run AK3 artifacts instead of using the API')
    args = parser.parse_args()
    if not args.repository or not args.token:
        parser.error('repository and token are required')
    if args.runs_json:
        try:
            runs = json.loads(args.runs_json)
        except json.JSONDecodeError as exc:
            parser.error(f'--runs-json is not valid JSON: {exc}')
        count = download_runs(args.repository, runs, args.token, args.destination)
    elif args.flatten_from:
        count = flatten_local(args.flatten_from, args.destination, args.expected_count)
    else:
        if not args.run_id:
            parser.error('--run-id is required unless --flatten-from is used')
        count = download_parent(
            args.repository, args.run_id, args.token, args.destination, args.expected_count
        )
    print(f'Prepared {count} AK3 archives in {args.destination}')


if __name__ == '__main__':
    main()
