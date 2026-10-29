#!/usr/bin/env python3
"""离线、限定目录的 H3 去重；不下载、安装或执行模型。"""

from path_validation import validate_model_root

import argparse
from contextlib import contextmanager, ExitStack
import hashlib
import os
from pathlib import Path
import stat
import sys
import uuid

from download_models import ROOT, load_catalog, verified_specs

IDS = ('h3-mlx-8bit', 'h3-ref2va-mlx-8bit')
LEGACY = 'video/h3-mlx-8bit'


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def open_directory(path):
    """从根目录逐级打开且不跟随符号链接，包含模型根目录。"""
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


def assert_directory(path, fd):
    with open_directory(path) as current:
        a, b = os.fstat(current), os.fstat(fd)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise ValueError('目录在核验期间改变')


@contextmanager
def verified_regular(path, spec):
    with open_directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size != spec['bytes']:
                raise ValueError('文件类型或字节数不符')
            digest = hashlib.sha256()
            while True:
                block = os.read(fd, 8 * 1024 * 1024)
                if not block:
                    break
                digest.update(block)
            if digest.hexdigest().lower() != spec['sha256'].lower():
                raise ValueError('完整 SHA256 不符')
            if signature(before) != signature(os.fstat(fd)):
                raise ValueError('文件在核验期间改变')
            assert_unchanged(path, parent, before)
            yield parent, before
        finally:
            os.close(fd)


def assert_unchanged(path, parent, info, allow_ctime=False):
    assert_directory(path.parent, parent)
    current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    old, new = signature(info), signature(current)
    if not stat.S_ISREG(current.st_mode) or (old[:4] != new[:4] if allow_ctime else old != new):
        raise ValueError('文件在核验后改变')


def replace_duplicate(source, target, spec, source_parent, source_info):
    """原子替换验证完成前保留同目录硬链接备份。"""
    with verified_regular(target, spec) as (parent, info):
        assert_unchanged(source, source_parent, source_info)
        if (info.st_dev, info.st_ino) == (source_info.st_dev, source_info.st_ino):
            return 0  # 已经是硬链接，没有需要去重的独立副本。
        if info.st_nlink != 1:
            raise ValueError('目标有其他硬链接，保留不改')
        token = uuid.uuid4().hex
        backup, temporary = '.h3-merge-backup-' + token, '.h3-merge-link-' + token
        relative = os.path.relpath(source, target.parent)
        backed_up = installed = False
        link_info = None
        try:
            os.link(target.name, backup, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            backed_up = True
            # 硬链接会改变 ctime，继续核对原先验证的 inode 和字节信息。
            assert_unchanged(target, parent, info, allow_ctime=True)
            if signature(os.stat(backup, dir_fd=parent, follow_symlinks=False))[:4] != signature(info)[:4]:
                raise ValueError('备份期间文件改变')
            os.symlink(relative, temporary, dir_fd=parent)
            link_info = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
            assert_unchanged(source, source_parent, source_info)
            assert_unchanged(target, parent, info, allow_ctime=True)
            os.replace(temporary, target.name, src_dir_fd=parent, dst_dir_fd=parent)
            installed = True
            assert_directory(target.parent, parent)
            assert_unchanged(source, source_parent, source_info)
            current = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (link_info.st_dev, link_info.st_ino):
                raise ValueError('替换后目标改变')
            os.unlink(backup, dir_fd=parent)
            backed_up = False
            return spec['bytes']
        except BaseException:
            if backed_up:
                current = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
                if installed and link_info and (current.st_dev, current.st_ino) == (link_info.st_dev, link_info.st_ino):
                    os.replace(backup, target.name, src_dir_fd=parent, dst_dir_fd=parent)
                    backed_up = False
                elif not installed and (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino):
                    os.unlink(backup, dir_fd=parent)
                    backed_up = False
                if backed_up:
                    print('保留恢复备份（目标被并发修改）：' + str(target.parent / backup), file=sys.stderr)
            raise
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def merge(models_dir, models, dry_run=False):
    base = validate_model_root(models_dir)
    if base == Path('/') or any(ord(c) < 32 or ord(c) == 127 for c in str(base)):
        raise ValueError('模型目录不能是根目录或包含控制字符')
    by_id = {m['id']: m for m in models}
    selected = [by_id[key] for key in IDS]
    specs = [verified_specs(m) for m in selected]
    common = {
        name: spec
        for name, spec in specs[0].items()
        if name in specs[1]
        and spec['bytes'] == specs[1][name]['bytes']
        and spec['sha256'].lower() == specs[1][name]['sha256'].lower()
    }
    folders = []
    result = dict(merged=0, already_shared=0, skipped=0, bytes_saved=0, detected=0)
    for relative in dict.fromkeys([m['subdir'] for m in selected] + [LEGACY]):
        rel = Path(relative)
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('清单目录越界')
        folder = base / rel
        try:
            with open_directory(folder):
                pass
            folders.append(folder)
            print('检测到：' + str(folder), flush=True)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            result['skipped'] += 1
            print('跳过不安全目录：' + str(folder) + '（' + str(exc) + '）')
    result['detected'] = len(folders)
    if len(folders) < 2:
        print('缺少本地另一份 H3 目录，无需合并；本操作不下载。普通下载菜单会自动复用已有文件。')
        return result
    print('请勿同时下载、移动或修改这两个模型目录。', flush=True)
    print('正在完整核验共享文件的 SHA256；大权重需要时间。' + ('仅预览，不修改文件。' if dry_run else ''), flush=True)
    for name, spec in common.items():
        print('核验共享文件：' + name, flush=True)
        with ExitStack() as stack:
            source = None
            # 保持已验证源文件描述符打开，避免重复完整散列。
            for folder in folders:
                candidate = folder / name
                try:
                    source_parent, source_info = stack.enter_context(verified_regular(candidate, spec))
                    source = candidate
                    break
                except (OSError, ValueError):
                    continue
            if source is None:
                result['skipped'] += len(folders)
                continue
            for folder in folders:
                target = folder / name
                if target == source:
                    continue
                try:
                    with open_directory(target.parent) as parent:
                        item = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
                        if stat.S_ISLNK(item.st_mode):
                            # 仅接受直接指向已核验普通源文件的相对链接。
                            link = os.readlink(target.name, dir_fd=parent)
                            if os.path.isabs(link) or os.path.normpath(str(target.parent / link)) != str(source):
                                raise ValueError('链接不是当前已核验的直接共享源，保留不改')
                            assert_unchanged(source, source_parent, source_info)
                            result['already_shared'] += 1
                            continue
                    if dry_run:
                        with verified_regular(target, spec) as (_, info):
                            saved = spec['bytes'] if info.st_nlink == 1 else 0
                    else:
                        saved = replace_duplicate(source, target, spec, source_parent, source_info)
                    result['merged' if saved else 'already_shared'] += 1
                    result['bytes_saved'] += saved
                except (OSError, ValueError) as exc:
                    result['skipped'] += 1
                    print('跳过：' + str(target) + '（' + str(exc) + '）', flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='自动检测并合并本地 H3 相同共享文件；不下载、不执行模型。')
    parser.add_argument(
        '--models-dir', type=str, default=Path.home() / 'AI/models', help='共享模型根目录，默认 ~/AI/models'
    )
    parser.add_argument('--dry-run', action='store_true', help='完整核验但不修改任何文件')
    args = parser.parse_args(argv)
    try:
        result = merge(args.models_dir, load_catalog(ROOT / 'catalog.json'), args.dry_run)
        print(
            ('预览：' if args.dry_run else '完成：')
            + f"合并 {result['merged']} 个；已共享 {result['already_shared']} 个；跳过 {result['skipped']} 个；"
            + ('预计' if args.dry_run else '')
            + f"去重文件逻辑大小 {result['bytes_saved']} 字节（{result['bytes_saved'] / 10**9:.2f} GB）。"
        )
        print('两个模型目录和差异文件保持原位；共享源被删除或移动会使链接失效。')
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print('无法安全合并：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
