#!/usr/bin/env python3
"""Fast, offline build contract tests; not a substitute for device boot tests."""
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


selection = load('select_configs')
release = load('prepare_release')
common_filter = load('filter_susfs_common_patch')


class PipelineTests(unittest.TestCase):
    def test_source_lock_and_public_certificate(self):
        lock = json.loads((ROOT / 'sources.lock.json').read_text())
        self.assertEqual(lock['zsu_repository'], 'Only7rb-coder/zsu')
        self.assertEqual(lock['zsu_manager_package'], 'com.zsu.zsu')
        self.assertRegex(lock['manager_certificate_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(int(lock['manager_certificate_size'], 16), 792)
        for sha in [lock['zsu_commit'], lock['upstream_build_commit'], *lock['susfs_commits'].values()]:
            self.assertRegex(sha, r'^[0-9a-f]{40}$')

    def test_device_configuration_matrix(self):
        all_configs = selection.select(ROOT, 'all', '')
        self.assertGreaterEqual(len(all_configs), 150)
        self.assertEqual(len(all_configs), len(list((ROOT / 'configs').rglob('*.json'))))
        identities = [(c['model'], c['os_version'], c['kernel_version']) for c in all_configs]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertTrue(all(c['uname'] == 'OP-ZSU' for c in all_configs))
        for group in ('OOS14', 'OOS15', 'OOS16', 'android12-5.10', 'android13-5.15', 'android14-6.1', 'android15-6.6', 'android16-6.12'):
            self.assertGreater(len(selection.select(ROOT, group, '')), 0)
        chosen = selection.select(ROOT, 'OOS14', 'configs/oos16/OP12-6.1.118.json')
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]['model'], 'OP12-6.1.118')
        self.assertEqual(chosen[0]['os_version'], 'OOS16')

    def test_bad_paths_rejected(self):
        for path in ('../zsu/kernel/Kbuild', '/etc/passwd', 'configs/../README.md', 'configs/oos16/missing.json'):
            with self.assertRaises(ValueError):
                selection.select(ROOT, 'all', path)
        with self.assertRaises(ValueError):
            selection.select(ROOT, 'invalid', '')

    def test_release_rejects_wrong_image_or_missing_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'AK3_TEST_ZSU.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('Image', b'not a kernel')
                archive.writestr('anykernel.sh', '#!/bin/sh\n')
            with self.assertRaises(ValueError):
                release.verify_zip(path)
            image = bytearray(6102400)
            image[56:60] = b'ARM\x64'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('Image', image)
                archive.writestr('anykernel.sh', '#!/bin/sh\n')
            self.assertRegex(release.verify_zip(path), r'^[0-9a-f]{64}$')
            with zipfile.ZipFile(path, 'a') as archive:
                archive.writestr('signing.keystore', b'forbidden')
            with self.assertRaises(ValueError):
                release.verify_zip(path)

    def test_no_manager_keys_or_upstream_framework_setup(self):
        for folder in ('scripts', 'patches', '.github'):
            for path in (ROOT / folder).rglob('*'):
                self.assertNotIn(path.suffix, ('.keystore', '.jks', '.pem'))
        action = (ROOT / '.github/actions/build-kernel/action.yml').read_text()
        self.assertNotIn('http.sslVerify false', action)
        self.assertNotIn('BakaSU/main/kernel/setup.sh', action)
        self.assertNotIn('tiann/KernelSU/main/kernel/setup.sh', action)
        self.assertNotIn('int __attribute__((weak)) kallsyms_on_each_symbol', action)
        integration = (ROOT / 'scripts/integrate_zsu.py').read_text()
        self.assertIn('KSU_MANAGER_PACKAGE', integration)
        self.assertIn('CONFIG_KSU_DEBUG is not set', integration)
        self.assertIn('CONFIG_KSU_DISABLE_MANAGER is not set', integration)

    def test_common_filter_preserves_susfs_declarations(self):
        patch = '\n'.join([
            'diff --git a/fs/stat.c b/fs/stat.c',
            '--- a/fs/stat.c', '+++ b/fs/stat.c', '@@ -1,3 +1,12 @@',
            ' #include <linux/fs.h>', '+#ifdef CONFIG_KSU_SUSFS',
            '+#include <linux/susfs_def.h>', '+#endif',
            '+#ifdef CONFIG_KSU_SUSFS',
            '+extern int ksu_handle_stat(int *fd);', '+#endif',
            '+#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT',
            '+extern bool susfs_is_inode_sus_kstat(struct inode *inode);', '+#endif',
            ' ', ' void original(void);',
            '@@ -10,1 +19,4 @@', ' original();',
            '+#ifdef CONFIG_KSU_SUSFS',
            '+if (is_su_session) ksu_handle_post_execveat_sucompat();', '+#endif', '',
        ])
        filtered = common_filter.transform(patch)
        self.assertIn('#include <linux/susfs_def.h>', filtered)
        self.assertIn('susfs_is_inode_sus_kstat', filtered)
        self.assertNotIn('ksu_handle_stat', filtered)
        self.assertNotIn('is_su_session', filtered)
        self.assertNotIn('ksu_handle_post_execveat_sucompat', filtered)

    def test_bridge_present(self):
        patch = ROOT / 'patches/zsu-susfs-v2.3.0.patch'
        self.assertTrue(patch.is_file(), 'SUSFS bridge must be implemented before building')
        self.assertIn('CONFIG_KSU_SUSFS', patch.read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
