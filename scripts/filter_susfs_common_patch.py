#!/usr/bin/env python3
"""Remove tiann-only root hooks while preserving independent SUSFS filesystem code."""
import argparse
from pathlib import Path
import re

DROP = re.compile(
    r'ksu_handle_(?:execveat|post_execveat|faccessat|stat|vfs_fstat|sys_read|setresuid|input_handle_event)'
    r'|ksu_su_compat_enabled|ksu_is_init_rc_hook_enabled|__ksu_is_allow_uid_for_current'
    r'|ksu_is_input_hook_enabled|ksu_install_su_fd|is_su_session'
)
HEADER = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)\n$')


def salvage_declarations(body):
    """Keep independent SUSFS headers/prototypes in the mixed stat header hunk."""
    result = []
    index = 0
    removed = False
    while index < len(body):
        line = body[index]
        if line.startswith('+#if'):
            depth = 1
            end = index + 1
            while end < len(body) and depth:
                if body[end].startswith('+#if'):
                    depth += 1
                elif body[end].startswith('+#endif'):
                    depth -= 1
                end += 1
            block = body[index:end]
            text = ''.join(x[1:] for x in block if x.startswith('+'))
            if DROP.search(text):
                if any(not x.startswith('+') for x in block) or '{' in text or 'goto ' in text:
                    return None
                removed = True
                index = end
                continue
        result.append(line)
        index += 1
    return result if removed else None


def transform(text):
    lines = text.splitlines(keepends=True)
    output = []
    index = 0
    dropped = 0
    while index < len(lines):
        if not lines[index].startswith('@@ '):
            output.append(lines[index])
            index += 1
            continue
        header = lines[index]
        index += 1
        body = []
        while index < len(lines) and not lines[index].startswith(('@@ ', 'diff --git ')):
            body.append(lines[index])
            index += 1
        additions = ''.join(x[1:] for x in body if x.startswith('+') and not x.startswith('+++'))
        if not DROP.search(additions):
            output.extend([header, *body])
            continue
        dropped += 1
        if 'extern bool susfs_is_inode_sus_kstat' in additions:
            kept = salvage_declarations(body)
            if kept is None:
                raise ValueError('Mixed SUSFS/manual declaration hunk needs an explicit port')
            match = HEADER.match(header)
            if not match:
                raise ValueError('Invalid unified patch header')
            old_count = sum(x.startswith((' ', '-')) for x in kept)
            new_count = sum(x.startswith((' ', '+')) for x in kept)
            output.append(f'@@ -{match[1]},{old_count} +{match[3]},{new_count} @@{match[5]}\n')
            output.extend(kept)
    if not dropped:
        raise ValueError('No manual-hook hunks matched; refuse an unvalidated patch format')
    result = ''.join(output)
    if any(DROP.search(line[1:]) for line in result.splitlines() if line.startswith('+') and not line.startswith('+++')):
        raise ValueError('Incompatible manual-hook additions remain')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('input')
    parser.add_argument('output')
    args = parser.parse_args()
    Path(args.output).write_text(transform(Path(args.input).read_text()))
