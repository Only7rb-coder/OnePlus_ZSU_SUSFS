#!/usr/bin/env python3
"""Fast, offline build contract tests; not a substitute for device boot tests."""
import importlib.util
import hashlib
import json
from pathlib import Path
import re
import tarfile
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
integration = load('integrate_zsu')


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

    def test_zsu_owns_native_selinux_hiding(self):
        patch = ''
        for name, symbol in [('hooks.c', 'fake_state'), ('selinuxfs.c', 'fake_status'), ('avc.c', 'susfs_ksu_sid')]:
            patch += '\n'.join([
                f'diff --git a/security/selinux/{name} b/security/selinux/{name}',
                f'--- a/security/selinux/{name}', f'+++ b/security/selinux/{name}',
                '@@ -1,1 +1,2 @@', ' existing;', f'+extern int {symbol};', '',
            ])
        filtered = common_filter.transform(patch)
        self.assertNotIn('fake_state', filtered)
        self.assertNotIn('fake_status', filtered)
        self.assertNotIn('a/security/selinux/hooks.c', filtered)
        self.assertNotIn('a/security/selinux/selinuxfs.c', filtered)
        self.assertIn('susfs_ksu_sid', filtered)

    def test_mediatek_and_direct_kernel_links(self):
        for symbolic_common in (False, True):
            with self.subTest(symbolic_common=symbolic_common), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                platform = root / 'kernel_platform'
                platform.mkdir()
                physical = root / 'kernel-5.10' if symbolic_common else platform / 'common'
                (physical / 'drivers').mkdir(parents=True)
                common = platform / 'common'
                if symbolic_common:
                    common.symlink_to('../kernel-5.10')
                repo = platform / 'KernelSU'
                (repo / 'kernel').mkdir(parents=True)
                (repo / 'kernel/Kconfig').write_text('config KSU\n    bool "ZSU"\n')
                drivers = integration.link_kernel(common, repo)
                self.assertTrue((drivers / 'kernelsu/Kconfig').is_file())
                self.assertEqual((drivers / 'kernelsu').resolve(), repo / 'kernel')

    def test_bridge_present(self):
        patch = ROOT / 'patches/zsu-susfs-v2.3.0.patch'
        self.assertTrue(patch.is_file(), 'SUSFS bridge must be implemented before building')
        text = patch.read_text()
        self.assertIn('CONFIG_KSU_SUSFS', text)
        self.assertIn('CMD_SUSFS_SET_PRIORITY_UID_FILTER', text)
        self.assertIn('susfs_set_priority_uid_filter(arg);', text)
        self.assertIn('kernel/manager/pkg_observer.c', text)
        self.assertIn('susfs_parse_priority_app_strings();', text)

    def test_priority_uid_filter_is_pinned_for_every_kernel_family(self):
        lock = json.loads((ROOT / 'sources.lock.json').read_text())
        self.assertEqual(set(lock['susfs_commits']), {
            'android12-5.10', 'android13-5.15', 'android14-6.1',
            'android15-6.6', 'android16-6.12',
        })
        self.assertEqual(set(lock['susfs_archive_sha256']), set(lock['susfs_commits'].values()))
        for family, commit in lock['susfs_commits'].items():
            archive_path = ROOT / 'vendor/susfs-archives' / f'susfs4ksu-{commit}.tar.gz'
            self.assertTrue(archive_path.is_file(), f'missing archive for {family}')
            self.assertEqual(
                hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                lock['susfs_archive_sha256'][commit],
            )
            with tarfile.open(archive_path, 'r:gz') as archive:
                names = archive.getnames()
                kernel_patch = next(
                    name for name in names
                    if name.endswith(f'/kernel_patches/50_add_susfs_in_gki-{family}.patch')
                )
                ksu_patch = next(
                    name for name in names
                    if name.endswith('/kernel_patches/KernelSU/10_enable_susfs_for_ksu.patch')
                )
                susfs_source = next(
                    name for name in names if name.endswith('/kernel_patches/fs/susfs.c')
                )
                header = next(
                    name for name in names
                    if name.endswith('/kernel_patches/include/linux/susfs.h')
                )
                kernel_text = archive.extractfile(kernel_patch).read().decode()
                ksu_text = archive.extractfile(ksu_patch).read().decode()
                susfs_text = archive.extractfile(susfs_source).read().decode()
                header_text = archive.extractfile(header).read().decode()
                self.assertIn('susfs_is_priority_uid_filtered', kernel_text, family)
                self.assertIn('CMD_SUSFS_SET_PRIORITY_UID_FILTER', ksu_text, family)
                self.assertIn('susfs_parse_priority_app_strings', ksu_text, family)
                self.assertIn('susfs_set_priority_uid_filter', susfs_text, family)
                self.assertIn('SUSFS_VERSION "v2.3.0"', header_text, family)
                self.assertTrue(any(name.endswith('/LICENSE') for name in names), family)


if __name__ == '__main__':
    unittest.main(verbosity=2)
