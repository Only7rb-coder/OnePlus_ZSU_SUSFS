#!/usr/bin/env python3
"""Recover only exact, known v2.3.0 header hunks; leave every other reject fatal."""
from pathlib import Path
import sys

TASK_MMU = '''#if defined(CONFIG_KSU_SUSFS_SUS_KSTAT) || defined(CONFIG_KSU_SUSFS_SUS_MAP) || defined(CONFIG_KSU_SUSFS_OPEN_REDIRECT)
#include <linux/susfs_def.h>
#endif // #if defined(CONFIG_KSU_SUSFS_SUS_KSTAT) || defined(CONFIG_KSU_SUSFS_SUS_MAP) || defined(CONFIG_KSU_SUSFS_OPEN_REDIRECT)
'''
NAMESPACE = '''#ifdef CONFIG_KSU_SUSFS
#include <linux/susfs_def.h>
#endif // #ifdef CONFIG_KSU_SUSFS
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
extern bool susfs_is_current_ksu_domain(void);
extern struct static_key_true susfs_is_sdcard_android_data_not_decrypted;

#define CL_COPY_MNT_NS BIT(25) /* used by copy_mnt_ns() */
#endif // #ifdef CONFIG_KSU_SUSFS_SUS_MOUNT

'''
ALLOWED = {
    'fs/proc/task_mmu.c': (TASK_MMU, '#include <linux/pkeys.h>'),
    'fs/namespace.c': (NAMESPACE, '#include "internal.h"'),
}


def repair(root):
    repaired = []
    for relative, (expected, anchor) in ALLOWED.items():
        source = root / relative
        reject = source.with_name(source.name + '.rej')
        if not reject.exists():
            continue
        lines = reject.read_text().splitlines(keepends=True)
        if sum(line.startswith('@@ ') for line in lines) != 1:
            raise ValueError(f'{relative}: reject is not exactly one known header hunk')
        body = lines[next(i for i, line in enumerate(lines) if line.startswith('@@ ')) + 1:]
        if any(line.startswith('-') for line in body):
            raise ValueError(f'{relative}: reject removes code; manual port required')
        added = ''.join(line[1:] for line in body if line.startswith('+'))
        if added != expected:
            raise ValueError(f'{relative}: reject differs from the audited header additions')
        content = source.read_text()
        needle = anchor + '\n'
        if content.count(needle) != 1:
            raise ValueError(f'{relative}: header insertion anchor is not unique')
        if expected not in content:
            # Never change an existing namespace flag or hide conflicting code.
            if relative == 'fs/namespace.c' and '#define CL_COPY_MNT_NS' in content:
                raise ValueError('Existing CL_COPY_MNT_NS needs an explicit compatibility review')
            content = content.replace(needle, needle + expected, 1)
            source.write_text(content)
        reject.unlink()
        repaired.append(relative)
    return repaired


if __name__ == '__main__':
    root = Path(sys.argv[1] if len(sys.argv) > 1 else '.').resolve(strict=True)
    for name in repair(root):
        print(f'Recovered exact SUSFS header-only context mismatch: {name}')
