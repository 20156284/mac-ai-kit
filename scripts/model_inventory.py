#!/usr/bin/env python3
"""只读查看清单内本地模型；只检查文件元数据，不读取权重、不联网。"""

from path_validation import validate_model_root

import argparse
import os
from pathlib import Path
import stat
import sys

from api_inventory import safe_root, relative_path, no_link_parents, inspect_model
from download_models import ROOT, load_catalog, expand_dependencies, visible_models, verified_specs
from merge_h3_models import IDS, LEGACY


def model_folder(base, model):
    return relative_path(base, model.get('subdir') or model['repo_id'])


def regular_info(path, base):
    """只看安全目录下的普通文件；不沿目录链接或文件链接访问。"""
    if not no_link_parents(path, base):
        return None
    try:
        info = path.lstat()
        return info if stat.S_ISREG(info.st_mode) else None
    except OSError:
        return None


def inspect_file(path, spec, base, shared_paths):
    info = regular_info(path, base)
    if info is not None:
        return info, info.st_size == spec.get('bytes'), False
    # 仅允许已知 H3 共享清单之间的直接相对符号链接，不跟随任意目标。
    if path not in shared_paths or not no_link_parents(path, base):
        return None, False, False
    try:
        if not path.is_symlink():
            return None, False, False
        link = os.readlink(path)
        if os.path.isabs(link):
            return None, False, False
        target = Path(os.path.normpath(str(path.parent / link)))
        if target not in shared_paths or shared_paths[target] != shared_paths[path]:
            return None, False, False
        info = regular_info(target, base)
        return info, bool(info and info.st_size == spec.get('bytes')), bool(info)
    except (OSError, ValueError):
        return None, False, False


def h3_files(base, models):
    selected = [m for m in models if m['id'] in IDS and m.get('enabled', True)]
    if len(selected) != 2:
        return {}, []
    a, b = [verified_specs(m) for m in selected]
    common = {
        name: spec
        for name, spec in a.items()
        if name in b and spec['bytes'] == b[name]['bytes'] and spec['sha256'].lower() == b[name]['sha256'].lower()
    }
    folders = list(dict.fromkeys([model_folder(base, m) for m in selected] + [base / LEGACY]))
    paths = {folder / name: (spec['bytes'], spec['sha256']) for folder in folders for name, spec in common.items()}
    return paths, [(folder / name, spec, name) for name, spec in common.items() for folder in folders]


def scan(base, models):
    """只按固定清单查询文件；文件齐全仅表示尺寸匹配，不表示能推理。"""
    base = safe_root(validate_model_root(base))
    shared_paths, shared_files = h3_files(base, models)
    rows = []
    for model in visible_models(models):
        components = expand_dependencies([model], models)
        expected = {}
        present = False
        unknown = False
        for component in components:
            folder = model_folder(base, component)
            # 保留旧版 FL2VA 目录识别，避免已下载旧目录从列表消失。
            if component['id'] == IDS[0] and not folder.exists() and (base / LEGACY).is_dir():
                folder = base / LEGACY
            display_folder = folder
            if component.get('legacy_subdirs'):
                item = inspect_model(component, base)
                folder = item['path']
                display_folder = item.get('display_path', folder)
            if no_link_parents(display_folder / '_', base) and display_folder.is_dir():
                present = True
            specs = dict({} if component.get('final_only_adaptation') else (component.get('expected_files') or {}))
            specs.update(component.get('generated_expected_files') or {})
            unknown |= not bool(specs)
            for name, spec in specs.items():
                expected[relative_path(folder, name)] = spec
        if not present:
            continue
        total = good = shared = 0
        for path, spec in expected.items():
            info, matched, linked = inspect_file(path, spec, base, shared_paths)
            if info is not None:
                total += info.st_size
            good += int(matched)
            shared += int(linked)
        state = '文件齐全' if expected and good == len(expected) and not unknown else f'不完整 {good}/{len(expected)}'
        rows.append({'model': model, 'bytes': total, 'state': state, 'shared': shared})
    # 仅同清单 SHA256 与大小的独立普通副本计入待校验建议；硬链接、符号链接不重复计入。
    groups = {}
    for path, spec, relative in shared_files:
        info = regular_info(path, base)
        if info is not None and info.st_size == spec['bytes']:
            groups.setdefault((relative, spec['bytes'], spec['sha256']), []).append(info)
    estimate = count = 0
    for key, infos in groups.items():
        unique = {(i.st_dev, i.st_ino): i for i in infos}
        if len(unique) > 1:
            # 有额外硬链接的文件不能作为可释放副本；留作源即可。
            removable = sum(i.st_nlink == 1 for i in unique.values())
            duplicates = min(len(unique) - 1, removable)
            estimate += duplicates * key[1]
            count += duplicates
    return base, rows, estimate, count


def display(base, rows, estimate, count):
    print('模型目录：' + str(base))
    if not rows:
        print('未发现清单内已下载模型。')
        return
    for index, row in enumerate(rows, 1):
        name = row['model'].get('short_name') or row['model']['name']
        print(f"{index}. {name} | {row['bytes'] / 10**9:.2f} GB | {row['state']}")
    print('体积为清单文件逻辑大小；文件齐全仅核对大小，未验证推理。')
    if estimate:
        print(
            f'合并建议：H3 有 {count} 个疑似重复文件，校验后最多可去重 {estimate / 10**9:.2f} GB（非实际磁盘释放量）。'
        )
        print('选择“合并 H3 共享权重”后执行校验与合并。')
    else:
        print('合并建议：未发现可合并的独立 H3 副本（已共享文件不重复计算）。')


def main(argv=None):
    parser = argparse.ArgumentParser(description='查看已下载模型与合并建议；只读，不安装、不下载。')
    parser.add_argument('--models-dir', type=str, default=Path.home() / 'AI/models', help='模型根目录')
    args = parser.parse_args(argv)
    try:
        args.models_dir = validate_model_root(args.models_dir)
        if not args.models_dir.expanduser().exists():
            print('模型目录：' + str(args.models_dir.expanduser()))
            print('目录尚不存在，未发现已下载模型。')
            return 0
        display(*scan(args.models_dir, load_catalog(ROOT / 'catalog.json')))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print('无法检查模型：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
