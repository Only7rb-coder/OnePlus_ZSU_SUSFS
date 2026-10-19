#!/usr/bin/env python3
"""Standalone tests for retry-only release helpers."""
import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


selection = load('select_configs')
downloader = load('release_download')


class RetryReleaseTests(unittest.TestCase):
    def test_retry_maps_only_failed_kernel_jobs_and_checks_lock(self):
        configs = selection.select(ROOT, 'all', '')[:2]
        lock = json.loads((ROOT / 'sources.lock.json').read_text())
        jobs = [
            {'name': f"{configs[0]['model']} / {configs[0]['os_version']} / {configs[0]['kernel_version']}",
             'conclusion': 'failure', 'status': 'completed'},
            {'name': f"{configs[1]['model']} / {configs[1]['os_version']} / {configs[1]['kernel_version']}",
             'conclusion': 'success', 'status': 'completed'},
            {'name': 'prepare', 'conclusion': 'success', 'status': 'completed'},
        ]
        encoded = base64.b64encode(json.dumps(lock).encode()).decode()

        def fake_api(url, token):
            if '/actions/runs/123/jobs?' in url:
                return {'jobs': jobs}
            if '/actions/runs/123' in url:
                return {'status': 'completed', 'conclusion': 'failure', 'head_sha': 'a' * 40,
                        'repository': {'full_name': 'owner/repo'}}
            if '/contents/sources.lock.json?' in url:
                return {'encoding': 'base64', 'content': encoded}
            raise AssertionError(url)

        with mock.patch.object(selection, '_github_json', side_effect=fake_api):
            result = selection.select_retry(ROOT, 'owner/repo', '123', 'token')
        self.assertEqual(result['parent_count'], 2)
        self.assertEqual(result['parent_success_count'], 1)
        self.assertEqual(len(result['configs']), 1)
        self.assertEqual(result['configs'][0]['model'], configs[0]['model'])

    def test_flatten_replaces_same_name_and_rejects_count(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'retry-assets' / 'nested'
            dest = root / 'release-assets'
            source.mkdir(parents=True)
            dest.mkdir()
            (dest / 'AK3_same.zip').write_bytes(b'parent')
            (source / 'AK3_same.zip').write_bytes(b'retry')
            self.assertEqual(downloader.flatten_local(source.parent, dest, 1), 1)
            self.assertEqual((dest / 'AK3_same.zip').read_bytes(), b'retry')
            with self.assertRaises(downloader.DownloadError):
                downloader.flatten_local(source.parent, dest, 2)

    def test_archive_member_path_is_not_flattened_unsafely(self):
        with self.assertRaises(downloader.DownloadError):
            downloader._archive_member_name('../AK3_escape.zip')
        self.assertEqual(downloader._archive_member_name('AK3_ok.zip'), 'AK3_ok.zip')
        self.assertIsNone(downloader._archive_member_name('debug.zip'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
