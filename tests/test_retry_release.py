#!/usr/bin/env python3
"""Standalone tests for retry-only release helpers."""
import base64
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


selection = load('select_configs')
downloader = load('release_download')


class RetryReleaseTests(unittest.TestCase):
    def _api_for(self, runs, jobs):
        lock = json.loads((ROOT / 'sources.lock.json').read_text())
        encoded = base64.b64encode(json.dumps(lock).encode()).decode()

        def fake_api(url, token):
            for run_id, run in runs.items():
                if f'/actions/runs/{run_id}/jobs?' in url:
                    return {'jobs': jobs[run_id] if url.endswith('page=1') else []}
                if url.endswith(f'/actions/runs/{run_id}'):
                    return run
            if '/contents/sources.lock.json?' in url:
                return {'encoding': 'base64', 'content': encoded}
            raise AssertionError(url)
        return fake_api

    @staticmethod
    def _job(config, conclusion):
        return {
            'name': f"{config['model']} / {config['os_version']} / {config['kernel_version']}",
            'conclusion': conclusion, 'status': 'completed',
        }

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

    def test_cancelled_parent_requires_exact_full_matrix(self):
        configs = selection.select(ROOT, 'all', '')
        jobs = {900: [self._job(config, 'cancelled') for config in configs]}
        runs = {900: {'status': 'completed', 'conclusion': 'cancelled',
                       'head_sha': 'c' * 40, 'repository': {'full_name': 'owner/repo'}}}
        with mock.patch.object(selection, '_github_json', side_effect=self._api_for(runs, jobs)):
            result = selection.select_retry(ROOT, 'owner/repo', '900', 'token')
        self.assertEqual(result['release_count'], 158)
        self.assertEqual(len(result['configs']), 158)
        self.assertEqual(result['release_only'], False)

        short_jobs = {901: jobs[900][:2]}
        short_runs = {901: {'status': 'completed', 'conclusion': 'cancelled',
                            'head_sha': 'd' * 40, 'repository': {'full_name': 'owner/repo'}}}
        with mock.patch.object(selection, '_github_json', side_effect=self._api_for(short_runs, short_jobs)):
            with self.assertRaisesRegex(ValueError, 'exact current 158-job matrix'):
                selection.select_retry(ROOT, 'owner/repo', '901', 'token')

    def test_reuse_successes_cover_targets_and_out_of_parent_is_fatal(self):
        configs = selection.select(ROOT, 'all', '')[:3]
        parent_jobs = {910: [self._job(configs[0], 'failure'), self._job(configs[1], 'failure')]}
        reuse_jobs = {911: [self._job(configs[0], 'success'), self._job(configs[1], 'failure')]}
        runs = {
            910: {'status': 'completed', 'conclusion': 'failure', 'head_sha': 'e' * 40,
                  'repository': {'full_name': 'owner/repo'}},
            911: {'status': 'completed', 'conclusion': 'failure', 'head_sha': 'f' * 40,
                  'repository': {'full_name': 'owner/repo'}},
        }
        jobs = {**parent_jobs, **reuse_jobs}
        with mock.patch.object(selection, '_github_json', side_effect=self._api_for(runs, jobs)):
            result = selection.select_retry(ROOT, 'owner/repo', '910', 'token', '911')
        self.assertEqual(len(result['configs']), 1)
        self.assertEqual(result['retained_success_count'], 1)
        self.assertEqual(result['retained_runs'][1]['source_pipeline_sha'], 'f' * 40)

        out_jobs = {912: [self._job(configs[2], 'success')]}
        out_runs = {912: {'status': 'completed', 'conclusion': 'success', 'head_sha': '1' * 40,
                          'repository': {'full_name': 'owner/repo'}}}
        bad_jobs = {910: parent_jobs[910], **out_jobs}
        bad_runs = {910: runs[910], **out_runs}
        with mock.patch.object(selection, '_github_json', side_effect=self._api_for(bad_runs, bad_jobs)):
            with self.assertRaisesRegex(ValueError, 'outside parent retry targets'):
                selection.select_retry(ROOT, 'owner/repo', '910', 'token', '912')

    def test_download_runs_requires_each_count_and_replaces_in_order(self):
        def container(payload):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('AK3_same.zip', payload)
            return stream.getvalue()

        artifacts = {
            1: [{'id': 1, 'name': 'AK3_same.zip', 'expired': False}],
            2: [{'id': 2, 'name': 'AK3_same.zip', 'expired': False}],
        }
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(downloader, 'list_artifacts', side_effect=lambda r, i, t: artifacts[int(i)]), \
                 mock.patch.object(downloader, 'github_bytes', side_effect=lambda u, t: container(b'one' if '/1/' in u else b'two')):
                total = downloader.download_runs('owner/repo', [
                    {'run_id': '1', 'success_count': 1, 'source_pipeline_sha': 'a' * 40},
                    {'run_id': '2', 'success_count': 1, 'source_pipeline_sha': 'b' * 40},
                ], 'token', Path(folder))
            self.assertEqual(total, 2)
            self.assertEqual((Path(folder) / 'AK3_same.zip').read_bytes(), b'two')

    def test_cancelled_job_upload_is_excluded_by_success_identity(self):
        good = 'AK3_OP12_OOS14_android14-6.1.57_ZSU_33356_SuSFS_v2.3.0.zip'
        extra = 'AK3_OP13_OOS15_android15-6.6.89_ZSU_33356_SuSFS_v2.3.0.zip'
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr(good, b'good')
        artifacts = [{'id': 1, 'name': good, 'expired': False},
                     {'id': 2, 'name': extra, 'expired': False}]
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(downloader, 'list_artifacts', return_value=artifacts), \
                mock.patch.object(downloader, 'github_bytes', return_value=stream.getvalue()) as fetch:
            count = downloader.download_parent('owner/repo', '1', 'token', Path(folder), 1,
                                               ['AK3_OP12_OOS14_android14-6.1.'])
            self.assertEqual(count, 1)
            self.assertEqual((Path(folder) / good).read_bytes(), b'good')
            fetch.assert_called_once()
            with self.assertRaises(downloader.DownloadError):
                downloader.download_parent('owner/repo', '1', 'token', Path(folder), 1,
                                           ['AK3_OP15_OOS16_android16-6.12.'])

    def test_version_suffix_matches_reference_zip_naming(self):
        records = [{'conclusion': 'success', 'config': {
            'model': model, 'os_version': 'OOS16', 'android_version': 'android14',
            'kernel_version': '6.1'}} for model in ('OP12', 'OP12-6.1.118')]
        prefixes = selection._success_prefixes(records)
        self.assertEqual(prefixes, ['AK3_OP12_OOS16_android14-6.1.',
                                    'AK3_OP12_OOS16_android14-6.1.118_'])
        artifacts = [{'id': i, 'name': f'AK3_OP12_OOS16_android14-{version}_ZSU.zip', 'expired': False}
                     for i, version in enumerate(('6.1.118', '6.1.134'), 1)]
        def container(url, token):
            name = artifacts[int(url.split('/')[-2]) - 1]['name']
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr(name, b'test')
            return stream.getvalue()
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(downloader, 'list_artifacts', return_value=artifacts), \
                mock.patch.object(downloader, 'github_bytes', side_effect=container):
            self.assertEqual(downloader.download_parent('owner/repo', '1', 'token', Path(folder), 2, prefixes), 2)

    def test_archive_member_path_is_not_flattened_unsafely(self):
        with self.assertRaises(downloader.DownloadError):
            downloader._archive_member_name('../AK3_escape.zip')
        self.assertEqual(downloader._archive_member_name('AK3_ok.zip'), 'AK3_ok.zip')
        self.assertIsNone(downloader._archive_member_name('debug.zip'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
