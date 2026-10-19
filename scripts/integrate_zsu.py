#!/usr/bin/env python3
"""Integrate the locked ZSU kernel, never upstream setup.sh or private key files."""
import json
import os
from pathlib import Path
import subprocess


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=False)


def kbuild_include_flags(repo):
    kernel = (repo / 'kernel').resolve(strict=True)
    if any(character.isspace() for character in str(kernel)):
        raise ValueError('Kernel include path contains unsupported whitespace')
    # 6.12's src already contains the source-tree prefix. ZSU's older
    # $(srctree)/$(src) expression doubles it for in-tree subdirectories.
    return ('\n# Physical source includes for out-of-tree object directories.\n'
            f'ccflags-y += -I{kernel} -I{kernel / "include"}\n')


def link_kernel(common, repo):
    drivers = common.resolve(strict=True) / 'drivers'
    link = drivers / 'kernelsu'
    if link.is_symlink():
        link.unlink()
    if link.exists():
        raise SystemExit('Existing non-symlink drivers/kernelsu requires review')
    target = (repo / 'kernel').resolve(strict=True)
    link.symlink_to(os.path.relpath(target, drivers))
    if not (link / 'Kconfig').is_file():
        raise SystemExit('ZSU kernel symlink failed to resolve to Kconfig')
    return drivers


def main():
    workspace = Path(os.environ['GITHUB_WORKSPACE'])
    lock = json.loads((workspace / 'sources.lock.json').read_text())
    requested = os.environ.get('ZSU_REF', '')
    commit = lock['zsu_commit']
    if requested and requested != commit:
        raise SystemExit('Refusing an unvalidated ZSU revision; update sources.lock.json and port tests first')
    platform = Path(os.environ['KERNEL_PLATFORM_FOLDER']).resolve(strict=True)
    common = Path(os.environ['COMMON_KERNEL_FOLDER']).resolve(strict=True)
    repo = platform / 'KernelSU'
    repo.mkdir(exist_ok=False)
    run('git', 'init', str(repo))
    run('git', 'remote', 'add', 'origin', 'https://github.com/' + lock['zsu_repository'] + '.git', cwd=repo)
    run('git', 'sparse-checkout', 'init', '--cone', cwd=repo)
    run('git', 'sparse-checkout', 'set', 'kernel', 'uapi', cwd=repo)
    run('git', 'fetch', '--depth=1', 'origin', commit, cwd=repo)
    run('git', 'checkout', '--detach', 'FETCH_HEAD', cwd=repo)
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    if actual != commit:
        raise SystemExit('ZSU source commit mismatch')
    # Restrict the kernel's accepted manager to the regular ZSU package and cert.
    kbuild = repo / 'kernel/Kbuild'
    text = kbuild.read_text()
    text += '\n# OnePlus ZSU release policy: no generic or alternate manager certificate.\n'
    text += 'KSU_VERSION := 33356\nKSU_VERSION_TAG := v1.4.10\n'
    # Assign early, before upstream ccflags-y expansion uses these variables.
    prefix = f'''# OnePlus ZSU pinned build identity (public certificate, not a signing key).
KSU_VERSION := 33356
KSU_VERSION_TAG := v1.4.10
KSU_MANAGER_PACKAGE := {lock['zsu_manager_package']}
KSU_NEXT_MANAGER_SIZE := {lock['manager_certificate_size']}
KSU_NEXT_MANAGER_HASH := {lock['manager_certificate_sha256']}
'''
    kbuild.write_text(prefix + text)
    if os.environ.get('OP_SUSFS') == 'true':
        patch = workspace / 'patches/zsu-susfs-v2.3.0.patch'
        if not patch.is_file():
            raise SystemExit('Missing audited ZSU SUSFS bridge; refusing to produce mislabeled artifacts')
        run('git', 'apply', '--check', str(patch), cwd=repo)
        run('git', 'apply', str(patch), cwd=repo)
    with kbuild.open('a') as stream:
        stream.write(kbuild_include_flags(repo))
    drivers = link_kernel(common, repo)
    for file, entry in [
        (drivers / 'Makefile', 'obj-$(CONFIG_KSU) += kernelsu/'),
        (drivers / 'Kconfig', 'source "drivers/kernelsu/Kconfig"'),
    ]:
        if entry not in file.read_text():
            with file.open('a') as stream:
                stream.write('\n' + entry + '\n')
    with (common / 'arch/arm64/configs/gki_defconfig').open('a') as stream:
        stream.write('\nCONFIG_KSU=y\nCONFIG_KPROBES=y\nCONFIG_HAVE_SYSCALL_TRACEPOINTS=y\n'
                     '# CONFIG_KSU_DEBUG is not set\n# CONFIG_KSU_DISABLE_MANAGER is not set\n'
                     '# CONFIG_KSU_DISABLE_POLICY is not set\n')
    artifacts = Path(os.environ['ARTIFACTS_FOLDER'])
    metadata = artifacts / f"{os.environ['OP_MODEL']}_{os.environ['OP_OS_VERSION']}.txt"
    with metadata.open('a') as stream:
        stream.write('ZSU\n33356\n')
    with Path(os.environ['GITHUB_ENV']).open('a') as stream:
        stream.write(f'KSU_COMMIT_SHA={commit}\nKSUVER=33356\nNEED_HOOKS=false\n')
    print(f'Integrated ZSU {commit}; manager={lock["zsu_manager_package"]}')


if __name__ == '__main__':
    main()
