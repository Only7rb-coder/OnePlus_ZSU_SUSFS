#!/usr/bin/env python3
"""Select device configs for one finite GitHub Actions matrix."""
import json
import os
from pathlib import Path


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


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    matrix = select(root, os.environ.get('SELECTOR', 'all'), os.environ.get('CONFIG_PATH', ''))
    payload = json.dumps({'include': matrix}, separators=(',', ':'))
    print(f'Selected {len(matrix)} configurations')
    for config in matrix:
        print(config['config_path'])
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        stream.write(f'matrix={payload}\ncount={len(matrix)}\n')
