#!/usr/bin/env python3
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('downloads', ROOT / 'scripts/release_download.py')
downloads = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloads)


class ArtifactRedirectTests(unittest.TestCase):
    def test_signed_storage_receives_no_github_token(self):
        api = 'https://api.github.com/repos/example/repo/actions/artifacts/1/zip'
        storage = 'https://example.blob.core.windows.net/artifact?sig=public-test'
        opener = MagicMock()
        opener.open.side_effect = HTTPError(api, 302, 'Found', {'Location': storage}, None)
        with patch.object(downloads, 'build_opener', return_value=opener), \
                patch.object(downloads, 'urlopen', return_value=io.BytesIO(b'archive')) as follow:
            self.assertEqual(downloads.github_bytes(api, 'test-token'), b'archive')
        api_request = opener.open.call_args.args[0]
        self.assertEqual(api_request.get_header('Accept'), 'application/vnd.github+json')
        self.assertEqual(api_request.get_header('Authorization'), 'Bearer test-token')
        storage_request = follow.call_args.args[0]
        self.assertEqual(storage_request.full_url, storage)
        self.assertIsNone(storage_request.get_header('Authorization'))

    def test_non_https_storage_is_rejected(self):
        opener = MagicMock()
        opener.open.side_effect = HTTPError('https://api.github.com', 302, 'Found', {'Location': 'http://example.com'}, None)
        with patch.object(downloads, 'build_opener', return_value=opener), \
                patch.object(downloads, 'urlopen') as follow:
            with self.assertRaises(downloads.DownloadError):
                downloads.github_bytes('https://api.github.com', 'test-token')
        follow.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
