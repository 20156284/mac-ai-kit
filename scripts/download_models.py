#!/usr/bin/env python3
"""采用 MIT 许可证的 macOS 魔搭模型下载规划器；不执行推理或自动安装。"""
import argparse
import datetime as dt
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import uuid
from path_validation import validate_model_root

ROOT = Path(__file__).resolve().parents[1]
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


# 仅翻译本程序的显示文本，不修改 argparse 的全局翻译函数。
READINESS_LABELS = {
    'verified_components_and_pinned_runtime_source_not_mac_tested': '组件与固定版框架支持已核查，尚未在 Mac 实测',
    'verified_mlx_whisper_files_not_mac_tested': 'MLX Whisper 完整文件已核查，需独立 mlx-whisper，尚未在 Mac 实测',
    'unknown': '未知',
    'verified_audio_cpp_header_not_mac_tested': 'audio.cpp 包头及内嵌附件已核查，尚未在 Mac 实测',
    'verified_components_not_mac_tested': '组件已核查，尚未在 Mac 实测',
    'raw_requires_conversion': '原始权重，需要转换适配',
    'verified_mirror_version_compatibility_unconfirmed': '镜像已核查，版本兼容性未确认',
    'modelscope_mirror_unverified': '魔搭镜像尚未核实',
    'verified_mlx_serve_layout_not_mac_tested': 'MLX-Serve 文件布局已核查，尚未在 Mac 实测',
    'verified_mlx_bf16_mtp_adapted_not_mac_tested': 'BF16 主模型与 MTP 文件已核查；头部适配，未在 Mac 实测',
    'verified_mlx_bf16_files_mtp_absent_not_mac_tested': 'MLX BF16 文件已核查，不含 MTP，尚未在 Mac 实测',
}


def readiness_label(model):
    """将机器状态转换为中文显示，不改变清单内的状态标识。"""
    value = model.get('readiness', 'unknown')
    return READINESS_LABELS.get(value, '未识别状态（' + str(value) + '）')


class ChineseArgumentParser(argparse.ArgumentParser):
    """本工具专用中文帮助与参数错误；保持标准参数解析行为。"""
    def __init__(self, *args, **kwargs):
        kwargs['add_help'] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = '位置参数'
        self._optionals.title = '选项'
        self.add_argument('-h', '--help', action='help', help='显示此帮助信息并退出')

    def format_usage(self):
        return super().format_usage().replace('usage: ', '用法：', 1)

    def format_help(self):
        return super().format_help().replace('usage: ', '用法：', 1)

    def error(self, message):
        # 以下涵盖本程序使用的标准参数类型、互斥组和可缩写长选项。
        translations = {
            'unrecognized arguments: ': '无法识别的参数：',
            'expected one argument': '需要一个参数值',
            'not allowed with argument ': '不能与以下参数同时使用：',
            'invalid int value: ': '不是有效整数：',
            'ignored explicit argument ': '该选项不接受显式参数值：',
            'ambiguous option: ': '选项有歧义：',
            ' could match ': '，可能匹配：',
            'argument ': '参数 ',
        }
        for original, translated in translations.items():
            message = message.replace(original, translated)
        self.print_usage(sys.stderr)
        self.exit(2, f'{self.prog}：错误：{message}\n')


def safe_repo(value):
    if not isinstance(value, str) or len(value.split('/')) != 2 or not all(SAFE.fullmatch(p) and p not in ('.', '..') for p in value.split('/')):
        raise ValueError('仓库必须是 ModelScope owner/name，不接受 URL 或命令参数')
    return value


def target_path(base, relative):
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or any(p in ('.', '..') or not SAFE.fullmatch(p) for p in rel.parts):
        raise ValueError('subdir 必须为安全的相对目录')
    result = base.joinpath(rel)
    current = base
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('目标子目录不能经过符号链接')
    if not result.resolve().is_relative_to(base.resolve()):
        raise ValueError('目标目录越界')
    return result


def load_catalog(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not isinstance(data.get('models'), list):
        raise ValueError('catalog 要求 schema_version:1 和 models 数组')
    seen = set()
    for model in data['models']:
        key = model.get('id', '')
        if not SAFE.fullmatch(key) or key in seen:
            raise ValueError('模型 id 无效或重复')
        seen.add(key)
        if model.get('repo_id'):
            safe_repo(model['repo_id'])
        for field in ('include', 'exclude'):
            patterns = model.get(field, [])
            if not isinstance(patterns, list) or any(not isinstance(p, str) or not p or p.startswith('-') or '\x00' in p for p in patterns):
                raise ValueError('include/exclude 必须是安全的字符串数组')
    return data['models']


def expand_dependencies(selected, models):
    """解析无环依赖图，保持先依赖、后所选模型的顺序。"""
    by_id = {m['id']: m for m in models}
    resolved, visiting, visited = [], set(), set()
    def visit(model):
        key = model['id']
        if key in visiting:
            raise ValueError('依赖存在循环：' + key)
        if key in visited:
            return
        if not model.get('enabled', True) or not model.get('repo_id'):
            raise ValueError('依赖或所选模型未启用：' + key)
        requires = model.get('requires', [])
        if not isinstance(requires, list) or any(not isinstance(v, str) for v in requires):
            raise ValueError('requires 必须是模型 id 字符串数组：' + key)
        visiting.add(key)
        for dep in requires:
            if dep not in by_id:
                raise ValueError('未知依赖：' + dep + '（来自 ' + key + '）')
            visit(by_id[dep])
        visiting.remove(key)
        visited.add(key)
        resolved.append(model)
    for model in selected:
        visit(model)
    return resolved


def command(cli, model, dest, workers):
    rev = model.get('revision') or 'master'
    if not isinstance(rev, str) or not rev or rev.startswith('-') or '\x00' in rev:
        raise ValueError('revision 无效')
    args = [str(cli), 'download', safe_repo(model['repo_id']), '--repo-type', 'model', '--revision', rev, '--local-dir', str(dest), '--max-workers', str(workers)]
    for field in ('include', 'exclude'):
        for value in model.get(field, []):
            args.extend(['--' + field, value])
    return args


def file_inventory(folder, hashes):
    result = []
    for path in sorted(folder.rglob('*')):
        if path.is_symlink() or not path.is_file():
            continue
        entry = {'path': str(path.relative_to(folder)), 'bytes': path.stat().st_size}
        if hashes:
            h = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                    h.update(block)
            entry['sha256'] = h.hexdigest()
        result.append(entry)
    return result


def check_expected_files(model, inventory, hashes):
    """提供核查快照时据此校验；分支更新后须重新核查。"""
    expected = model.get('expected_files', {})
    by_path = {item['path']: item for item in inventory}
    for relative, spec in expected.items():
        actual = by_path.get(relative)
        if actual is None:
            raise ValueError('缺少清单要求文件：' + relative)
        variants = [spec, *spec.get('compatible_versions', [])]
        variants = [v for v in variants if actual['bytes'] == v.get('bytes')]
        if spec.get('bytes') is not None and not variants:
            raise ValueError('文件大小与核查快照不同，请复核仓库更新：' + relative)
        if hashes and spec.get('sha256') and not any(actual.get('sha256') == v.get('sha256') for v in variants):
            raise ValueError('SHA256与核查快照不同：' + relative)


def validate_plan_targets(plans):
    owners = {}
    for model, target, _ in plans:
        if target in owners:
            previous = owners[target]
            if (reuse_family(model) or reuse_family(previous) or
                    model['repo_id'] != previous['repo_id'] or
                    (model.get('revision') or 'master') != (previous.get('revision') or 'master')):
                raise ValueError('多个不同仓库或版本的下载目录重复')
        owners[target] = model


def reuse_family(model):
    """共享必须由清单显式声明，不能凭同名文件推断模型家族。"""
    return model.get('reuse_family') or (model.get('family') if model.get('family') == 'h3' else None)


def verified_specs(model):
    expected = model.get('expected_files', {})
    if not expected:
        raise ValueError('可复用模型必须有完整的 expected_files 核查快照')
    for relative, spec in expected.items():
        if not isinstance(relative, str) or '\\' in relative or any(p in ('', '.', '..') for p in relative.split('/')) or Path(relative).is_absolute():
            raise ValueError('核查文件路径不安全：' + str(relative))
        if not all(re.fullmatch(r'[A-Za-z0-9_.-]+', p) for p in relative.split('/')):
            raise ValueError('核查文件路径不安全：' + relative)
        if not isinstance(spec.get('bytes'), int) or spec['bytes'] < 0 or not re.fullmatch('[a-fA-F0-9]{64}', spec.get('sha256', '')):
            raise ValueError('可复用文件必须有 bytes 和完整 SHA256：' + relative)
        for version in spec.get('compatible_versions', []):
            if not isinstance(version.get('bytes'), int) or version['bytes'] < 0 or not re.fullmatch('[a-fA-F0-9]{64}', version.get('sha256', '')):
                raise ValueError('兼容版本必须有 bytes 和完整 SHA256：' + relative)
    return expected


def no_link_parents(path):
    """检查原始路径，不先 resolve 掩盖符号链接。"""
    for parent in [path, *path.parents]:
        if parent.is_symlink():
            raise ValueError('目标目录不能经过符号链接：' + str(parent))
        if parent.exists() and not parent.is_dir():
            raise ValueError('目标父路径不是目录：' + str(parent))


def digest_file(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verify_file(path, spec, strict=False):
    try:
        resolved = path.resolve(strict=True)
        variants = [spec, *spec.get('compatible_versions', [])]
        candidates = [v for v in variants if resolved.is_file() and resolved.stat().st_size == v['bytes']]
        digest = digest_file(resolved).lower() if candidates else None
        valid = any(digest == v['sha256'].lower() for v in candidates)
    except (OSError, RuntimeError):
        valid = False
    if strict and not valid:
        raise ValueError('文件缺失、断链或 SHA256 不符；不覆盖，请明确决定修复方式：' + str(path))
    return valid


def is_h3_reuse_model(model):
    """识别发布清单的实际家族，以及兼容的旧 h3 家族标记。"""
    return reuse_family(model) in ('minimax-h3-mlx-serve', 'h3')


def automatic_reuse_sources(base, models):
    """只探测当前根目录的清单 H3 路径和已分发的旧默认路径，不递归搜索。"""
    relatives = [m.get('subdir') or m['repo_id'] for m in models if is_h3_reuse_model(m)]
    if relatives:
        relatives.append('video/h3-mlx-8bit')
    sources = []
    for relative in dict.fromkeys(relatives):
        try:
            path = target_path(base, relative)
            no_link_parents(path)
            if path.is_dir():
                # 自动来源携带边界；不得顺着文件链接读取根目录以外的文件。
                sources.append((path, base.resolve()))
        except (ValueError, OSError, RuntimeError):
            continue
    return sources


def automatic_model_sources(base, model):
    """仅探测清单目录及仓库名、模型标识的常见旧布局，不递归扫描磁盘。"""
    repo = safe_repo(model['repo_id'])
    canonical = Path(model.get('subdir') or repo)
    names = {repo, repo.split('/')[1], model['id']}
    if canonical.name not in ('mtp', 'dependencies', 'licenses'):
        names.add(canonical.name)
    parents = {'', str(canonical.parent), 'llm', 'audio', 'music', 'images', 'video',
               'mlx', 'mlx-serve', 'audio-cpp', 'audio.cpp', 'sd-cpp',
               'audio/audio-cpp', 'audio/audio.cpp', 'music/audio-cpp', 'music/audio.cpp'}
    relatives = {str(canonical)} | {str(Path(parent) / name) for parent in parents for name in names}
    sources = [base] if base.is_dir() else []
    relatives.add(str(canonical.parent))
    relatives.discard('.')
    for relative in sorted(relatives):
        try:
            path = target_path(base, relative)
            no_link_parents(path)
            if path.is_dir():
                sources.append(path)
        except (ValueError, OSError, RuntimeError):
            continue
    return sources


def local_reusable_files(model, folder, sources, report=False):
    """识别同盘、完整 SHA 相同的旧文件；发布前须明确迁移，不能自动复制。"""
    expected, result = verified_specs(model), {}
    ancestor = folder
    while not ancestor.exists():
        ancestor = ancestor.parent
    device = ancestor.stat().st_dev
    for source in sources:
        if source == folder:
            continue
        for name, spec in expected.items():
            if name in result or (folder / name).exists() or (folder / name).is_symlink():
                continue
            path = source / name
            try:
                no_link_parents(path.parent)
                if path.is_symlink() or not path.is_file():
                    continue
                if path.stat().st_dev != device:
                    if report:
                        print('跨磁盘旧文件不自动复用：' + str(path), flush=True)
                    continue
                if report and spec['bytes'] >= 100 * 10**6:
                    print('校验旧目录：' + str(path), flush=True)
                if verify_file(path, spec):
                    result[name] = path
                elif report:
                    print('旧文件校验不符，保持原样：' + str(path), flush=True)
            except (ValueError, OSError, RuntimeError):
                continue
    return result


def rename_exclusive(source, target):
    """同目录原子发布且不覆盖；不使用会覆盖现有文件的普通 rename。"""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        fn = libc.renamex_np
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = fn(os.fsencode(source), os.fsencode(target), 0x4)  # RENAME_EXCL
    elif sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        fn = libc.renameat2
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = fn(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    else:
        raise OSError(errno.ENOTSUP, '当前系统无安全的不覆盖发布接口；保留暂存文件', str(source))
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def verified_publish_leftover(target, spec):
    """仅检查目标旁精确旧版临时文件格式；校验不符的文件不动。"""
    if not target.parent.is_dir():
        return None
    pattern = re.compile(r'^\.' + re.escape(target.name) + r'\.[0-9a-f]{32}\.publish$')
    for item in sorted(target.parent.iterdir()):
        if (pattern.fullmatch(item.name) and not item.is_symlink()
                and item.is_file() and verify_file(item, spec)):
            return item
    return None


def publication_reservation_matches(target, source, record):
    """按精确路径和随机标记认领占位；不依赖外置文件系统的合成 inode。"""
    try:
        if not isinstance(record, dict) or record.get('source') != str(source.absolute()):
            return False
        token = record.get('token')
        if not isinstance(token, str) or not re.fullmatch(
                r'Mac AI Kit publication reservation [0-9a-f]{32}', token):
            return False
        no_link_parents(target.parent)
        expected = token.encode('ascii')
        if not stat.S_ISREG(target.lstat().st_mode):
            return False
        # 不跟随链接；读取有上限，避免误把用户大文件或特殊文件作为占位。
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        with os.fdopen(os.open(target, flags), 'rb') as stream:
            current = os.fstat(stream.fileno())
            return (stat.S_ISREG(current.st_mode) and current.st_size == len(expected)
                    and stream.read(len(expected) + 1) == expected)
    except (OSError, ValueError, UnicodeError):
        return False


def owned_publication_reservation(target, source):
    """只读识别完整记录的中断占位；兼容 v33 不稳定的 dev/ino 记录。"""
    lock = target.with_name('.' + target.name + '.publish-lock')
    try:
        if lock.is_symlink() or not lock.is_file():
            return False
        record = json.loads(lock.read_text(encoding='utf-8'))
        return publication_reservation_matches(target, source, record)
    except (OSError, ValueError, UnicodeError):
        return False


def move_verified_portable(source, target, spec):
    """同盘移动。锁仅协调本工具；兼容路径不能阻止其他程序同时改写目录。"""
    if source.stat().st_dev != target.parent.stat().st_dev:
        raise OSError(errno.EXDEV, '发布仅允许同盘移动；不复制，已保留暂存文件', str(source))
    lock = target.with_name('.' + target.name + '.publish-lock')
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(lock, flags, 0o600), 'r+', encoding='utf-8') as journal:
        fcntl.flock(journal.fileno(), fcntl.LOCK_EX)
        no_link_parents(target.parent)
        text = journal.read()
        record = json.loads(text) if text else None
        # 旧日志的 dev/ino 仅为历史信息；随机标记与精确来源必须全部相符。
        if publication_reservation_matches(target, source, record):
            target.unlink()
        if target.exists() or target.is_symlink():
            raise FileExistsError(errno.EEXIST, '目标已存在，不覆盖；暂存文件保留', str(target))
        try:
            rename_exclusive(source, target)
        except OSError as exc:
            if exc.errno not in {errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.EINVAL, 45}:
                raise
            print('此磁盘不支持独占重命名；使用同盘移动，不复制、不保留第二份。移动期间请勿让其他程序修改此模型目录。', flush=True)
            token = 'Mac AI Kit publication reservation ' + uuid.uuid4().hex
            # xb 同时排除既有文件和悬空符号链接；绝不把旧目标当作占位文件。
            with target.open('xb') as placeholder:
                placeholder.write(token.encode())
                placeholder.flush()
                os.fsync(placeholder.fileno())
            record = {'source': str(source.absolute()), 'token': token, 'version': 2}
            journal.seek(0); journal.truncate()
            json.dump(record, journal); journal.flush(); os.fsync(journal.fileno())
            if not publication_reservation_matches(target, source, record):
                raise FileExistsError(errno.EEXIST, '发布占位检查未通过，停止移动；保留暂存文件', str(target))
            try:
                # POSIX 同盘 rename 不复制。最后占位检查和 rename 之间仍有外部写入竞态。
                os.replace(source, target)
            except BaseException:
                if publication_reservation_matches(target, source, record):
                    target.unlink()
                raise
        journal.seek(0); journal.truncate(); journal.flush()
    verify_file(target, spec, strict=True)


def publish_verified(source, target, spec, disposable=False):
    """只移动明确归本工具所有的暂存文件；不复制、不创建硬链接。"""
    no_link_parents(source.parent)
    no_link_parents(target.parent)
    if source.is_symlink():
        raise ValueError('发布来源不能是链接：' + str(source))
    verify_file(source, spec, strict=True)
    if not disposable:
        raise ValueError('旧目录文件保持原样；不自动移动、硬链接或复制。请选择已有模型目录，或明确迁移后重试：' + str(source))
    move_verified_portable(source, target, spec)


def mtp_source_candidates(model, folder, old_folder=None):
    """只读取明确 MTP 目录、精确所有权暂存目录；不扫描整盘。"""
    name = model['local_adaptations'][0]['source']
    roots = [folder]
    if old_folder is not None:
        old_folder = old_folder.expanduser().absolute()
        no_link_parents(old_folder)
        if not old_folder.is_dir():
            raise ValueError('指定旧 MTP 目录不存在：' + str(old_folder))
        roots.append(old_folder)
    for root in roots:
        yield root / name
        yield staging_directory(model, root) / name


def final_mtp_present(model, folder):
    """日常只检查最终文件路径和大小；不代表已验证可推理。"""
    no_link_parents(folder)
    for name, spec in model['generated_expected_files'].items():
        path = target_path(folder, name)
        no_link_parents(path.parent)
        if path.is_symlink():
            raise ValueError('MTP 最终文件不能是链接：' + str(path))
        if not path.exists():
            return False
        # 完整日志中的自有 MTP 占位不是最终权重；随后仍须重新核验 tmp。
        lock = path.with_name('.' + path.name + '.publish-lock')
        if lock.is_file() and not lock.is_symlink():
            try:
                record = json.loads(lock.read_text(encoding='utf-8'))
                if not isinstance(record, dict) or not isinstance(record.get('source'), str):
                    raise ValueError('无效发布记录')
                temporary = Path(record['source'])
                pattern = r'^\.' + re.escape(path.name) + r'\.[0-9a-f]{32}\.tmp$'
                if (temporary.parent == path.parent.absolute() and re.fullmatch(pattern, temporary.name)
                        and temporary.is_file() and not temporary.is_symlink()
                        and owned_publication_reservation(path, temporary)):
                    return False
            except (OSError, ValueError):
                pass
        if not path.is_file() or path.stat().st_size != spec['bytes']:
            raise ValueError('MTP 最终文件类型或大小不符；不覆盖：' + str(path))
    return True


def verified_adaptation_temporary(target, spec):
    """只接管精确命名、完整散列匹配的适配临时文件，优先匹配发布日志。"""
    pattern = re.compile(r'^\.' + re.escape(target.name) + r'\.[0-9a-f]{32}\.tmp$')
    candidates = sorted((item for item in target.parent.iterdir()
                         if pattern.fullmatch(item.name) and not item.is_symlink()
                         and item.is_file() and verify_file(item, spec))) if target.parent.is_dir() else []
    return next((item for item in candidates if owned_publication_reservation(target, item)),
                candidates[0] if candidates else None)


def final_mtp_preflight(model, folder, verify_sources=True, old_folder=None):
    """仅保留最终 MTP；下载原件只存在于有所有权标记的暂存目录。"""
    row = {'id': model['id'], 'existing': 0, 'network': 0, 'generated': 0,
           'additional': 0, 'peak_additional': 0, 'reuse': {}, 'local_reuse': {}, 'missing': []}
    final_bytes = sum(x['bytes'] for x in model['generated_expected_files'].values())
    if verify_sources and final_mtp_present(model, folder):
        row['existing'] = final_bytes
        return row
    rule = model['local_adaptations'][0]
    name = rule['source']
    spec = verified_specs(model)[name]
    source = folder / name
    found = False
    if verify_sources:
        for candidate in mtp_source_candidates(model, folder, old_folder):
            no_link_parents(candidate.parent)
            if candidate.is_symlink():
                raise ValueError('MTP 来源不能是链接：' + str(candidate))
            if candidate.exists():
                if candidate == source:
                    verify_file(candidate, spec, strict=True)
                    found = True
                else:
                    found = verify_file(candidate, spec)
                if found:
                    row['local_reuse'][name] = candidate
                    break
    if not found:
        row['network'] = spec['bytes']
        row['missing'] = [name]
    row['generated'] = row['additional'] = final_bytes
    row['peak_additional'] = final_bytes + row['network']
    target = target_path(folder, rule['target'])
    # 有已下载原件及精确格式 tmp 时，只读计算输出散列并校验；避免重试重复预算空间。
    if found and target.parent.is_dir() and any(target.parent.glob('.' + target.name + '.*.tmp')):
        results = adapt_local_weights(model, folder, verify_only=True,
                                      source_overrides={name: row['local_reuse'][name]})
        if results and verified_adaptation_temporary(target, results[0]) is not None:
            row['existing'] = final_bytes
            row['generated'] = row['additional'] = row['peak_additional'] = 0
            row['pending_publish'] = True
    return row


def download_final_mtp(cli, model, folder, workers, old_folder=None):
    """补齐单份最终 MTP；旧版用户原件不删除，新暂存原件成功后清理。"""
    if final_mtp_present(model, folder):
        return [dict(spec, path=name, check='路径与大小检查；未做推理验证')
                for name, spec in model['generated_expected_files'].items()]
    row = final_mtp_preflight(model, folder, old_folder=old_folder)
    folder.mkdir(parents=True, exist_ok=True)
    rule = model['local_adaptations'][0]
    name = rule['source']
    source = row['local_reuse'].get(name)
    stage = None
    if source is None:
        stage = staging_directory(model, folder, create=True)
        subprocess.run(command(cli, dict(model, include=[name], exclude=[]), stage, workers), check=True)
        source = stage / name
    elif source.parent == staging_directory(model, folder):
        stage = source.parent
    result = adapt_local_weights(model, folder, source_overrides={name: source})
    if stage is not None:
        staging_directory(model, folder)
        shutil.rmtree(stage)
    return result


def download_preflight(plans, sources, models, verify_sources=True, base=None, report=False, old_mtp_folder=None):
    """先核验已有文件，再分别计算网络下载量和新增磁盘量；不写模型文件。"""
    rows, seen = [], set()
    for model, folder, _ in plans:
        if model.get('final_only_adaptation'):
            row = final_mtp_preflight(model, folder, verify_sources, old_mtp_folder)
            rows.append(row)
            if report:
                if row.get('pending_publish'):
                    print('MTP：已验证完整暂存，待移动发布；无需重复下载或新增权重空间。', flush=True)
                elif not row['additional']:
                    print('MTP：已下载，无需重复下载。', flush=True)
                else:
                    print(f"MTP：需补 {row['network'] / 10**9:.2f} GB；最终新增 {row['additional'] / 10**9:.2f} GB。", flush=True)
            continue
        row = {'id': model['id'], 'existing': 0, 'network': 0, 'generated': 0,
               'additional': 0, 'reuse': {}, 'local_reuse': {}, 'missing': []}
        if not model.get('expected_files'):
            row['network'] = row['additional'] = float(model.get('disk_gb') or 0) * 10**9
            rows.append(row)
            continue
        specs = verified_specs(model)
        family = reuse_family(model)
        reusable = reusable_files(model, folder, sources, models) if family and sources and verify_sources else {}
        legacy = local_reusable_files(model, folder, automatic_model_sources(base, model), report) if base is not None and verify_sources else {}
        row['reuse'], row['local_reuse'] = reusable, legacy
        stage = staging_directory(model, folder) if verify_sources else None
        available = dict(reusable, **legacy)
        keys = set()
        for name, spec in specs.items():
            key = (family or model['id'], spec['bytes'], spec['sha256'].lower())
            keys.add(key)
            path = folder / name
            present = False
            if verify_sources:
                no_link_parents(path.parent)
                if (path.exists() or path.is_symlink()) and not owned_publication_reservation(path, verified_publish_leftover(path, spec) or stage / name):
                    if path.is_symlink() and not family:
                        raise ValueError('普通模型已有文件是链接；不跟随、不覆盖：' + str(path))
                    if report and spec['bytes'] >= 100 * 10**6:
                        print('校验已有：' + str(path), flush=True)
                    verify_file(path, spec, strict=True)
                    available[name] = path
                    present = True
                elif verified_publish_leftover(path, spec):
                    available[name] = verified_publish_leftover(path, spec)
                    present = True
                elif stage.exists() and verify_file(stage / name, spec):
                    available[name] = stage / name
                    present = True
            if present or name in available:
                row['existing'] += spec['bytes']
            elif key not in seen:
                row['network'] += spec['bytes']
                row['missing'].append(name)
        seen.update(keys)
        for rule in model.get('local_adaptations', []):
            target = target_path(folder, rule['target'])
            if verify_sources and (target.exists() or target.is_symlink()):
                source = available.get(rule['source'])
                if source is None:
                    raise ValueError('已有兼容文件但缺少可核验的源文件；不覆盖：' + str(target))
                adapt_local_weights(dict(model, local_adaptations=[rule]), folder,
                                    verify_only=True, source_overrides={rule['source']: source})
            else:
                row['generated'] += model.get('generated_expected_files', {}).get(rule['target'], {}).get('bytes', specs[rule['source']]['bytes'] + 4096)
        row['additional'] = row['network'] + row['generated']
        rows.append(row)
        if report:
            label = model.get('short_name', model.get('name', model['id']))
            if row['network'] == 0 and row['generated'] == 0:
                print(f'{label}：已下载，无需重复下载。', flush=True)
            else:
                print(f"{label}：已有 {row['existing'] / 10**9:.2f} GB；需补 {row['network'] / 10**9:.2f} GB"
                      + (f"；兼容副本 {row['generated'] / 10**9:.2f} GB" if row['generated'] else ''), flush=True)
            if model.get('runtime') == 'audio.cpp' and row['missing'] and 0 < row['network'] < 10**7:
                print(f"仅补组件 {row['network'] / 10**6:.2f} MB：" + '、'.join(row['missing']), flush=True)
            for parent in sorted({str(path.parent) for path in legacy.values()}):
                print('复用已有目录：' + parent, flush=True)
    return rows


def prioritize_existing_h3(plans):
    """仅重排 H3 槽位，已有版本优先；不扰动其他模型的依赖顺序。"""
    existing = iter(sorted((p for p in plans if is_h3_reuse_model(p[0])),
                           key=lambda p: not p[1].is_dir()))
    return [next(existing) if is_h3_reuse_model(p[0]) else p for p in plans]


def reusable_files(model, folder, sources, models):
    """只返回已逐字节核验的来源，不修改来源，也不链接目标目录内文件。"""
    expected = verified_specs(model)
    result = {}
    reserved_stage = staging_identity(model, folder)[0].resolve()
    peers = [m for m in models if reuse_family(m) == reuse_family(model)]
    for source in sources:
        boundary = None
        if isinstance(source, tuple):
            source, boundary = source
        source = source.expanduser().absolute()
        resolved_source, resolved_target = source.resolve(), folder.resolve()
        if resolved_source == resolved_target:
            continue
        if not source.is_dir():
            raise ValueError('复用源目录不存在或不是目录：' + str(source))
        if resolved_source.is_relative_to(resolved_target) or resolved_target.is_relative_to(resolved_source):
            raise ValueError('复用源与目标不能相互嵌套')
        if resolved_source == reserved_stage or resolved_source.is_relative_to(reserved_stage) or reserved_stage.is_relative_to(resolved_source):
            raise ValueError('复用源不能与受控暂存目录重叠')
        candidates = {}
        for peer in peers:
            for name, spec in verified_specs(peer).items():
                candidates.setdefault((spec['bytes'], spec['sha256'].lower()), []).append(name)
        for name, spec in expected.items():
            if name in result:
                continue
            for candidate in dict.fromkeys(candidates.get((spec['bytes'], spec['sha256'].lower()), [])):
                path = source / candidate
                if path.is_symlink():
                    try:
                        path.resolve(strict=True)
                    except (OSError, RuntimeError) as exc:
                        if boundary is not None:
                            continue
                        raise ValueError('复用源存在断链或链接循环；请先明确修复方式：' + str(path)) from exc
                try:
                    if boundary is not None and not path.resolve().is_relative_to(boundary):
                        continue
                    if path.resolve().is_relative_to(resolved_target) or path.resolve().is_relative_to(reserved_stage):
                        continue
                except (OSError, RuntimeError):
                    continue
                if verify_file(path, spec):
                    result[name] = path.resolve(strict=True)
                    break
    return result


def staging_identity(model, folder):
    identity = {'repo_id': model['repo_id'], 'revision': model.get('revision') or 'master',
                'destination': str(folder.absolute()), 'expected_files': verified_specs(model)}
    text = json.dumps(identity, sort_keys=True, ensure_ascii=False)
    key = hashlib.sha256(text.encode()).hexdigest()[:24]
    return folder.parent / ('.h3-stage-' + key), text


def staging_directory(model, folder, create=False):
    """只接管具有精确所有权标记的固定暂存目录；中断保留供客户端续传。"""
    stage, identity = staging_identity(model, folder)
    no_link_parents(stage)
    marker = stage / '.lean-ai-stage-owner.json'
    if not stage.exists():
        if not create:
            return stage
        stage.mkdir(mode=0o700, parents=False)
        with marker.open('x', encoding='utf-8') as stream:
            stream.write(identity)
    if marker.is_symlink() or not marker.is_file() or marker.read_text(encoding='utf-8') != identity:
        raise ValueError('暂存目录缺少匹配所有权标记；不接管、不删除：' + str(stage))
    for root, dirs, files in os.walk(stage, followlinks=False):
        if any((Path(root) / name).is_symlink() for name in dirs + files):
            raise ValueError('暂存目录含符号链接，拒绝写入：' + str(stage))
    return stage


def download_verified_bundle(cli, model, folder, workers, sources, models, local_reuse=None):
    """严格模式：暂存下载、强制 SHA、独占安装，客户端永不接触复用链接。"""
    expected = verified_specs(model)
    no_link_parents(folder)
    reuse = reusable_files(model, folder, sources, models) if reuse_family(model) else {}
    local_reuse = local_reuse or {}
    reuse.update(local_reuse)
    present = set()
    stage = staging_directory(model, folder)
    for name, spec in expected.items():
        path = folder / name
        no_link_parents(path.parent)
        if (path.exists() or path.is_symlink()) and not owned_publication_reservation(path, verified_publish_leftover(path, spec) or stage / name):
            if path.is_symlink() and not reuse_family(model):
                raise ValueError('普通模型已有文件是链接；不跟随、不覆盖：' + str(path))
            verify_file(path, spec, strict=True)
            present.add(name)
    legacy_pending = [name for name in local_reuse if name not in present]
    if legacy_pending:
        raise ValueError('旧目录文件保持原样；不自动移动、硬链接或复制。请选择已有模型目录，或明确迁移后重试：' + str(local_reuse[legacy_pending[0]]))
    missing = [name for name in expected if name not in present and name not in reuse]
    reused_bytes = sum(expected[name]['bytes'] for name in reuse if name not in present)
    print(f"已验证复用 {len(set(reuse) - present)} 个文件（{reused_bytes / 10**9:.2f} GB）；待补齐 {len(missing)} 个文件（已验证暂存文件不重复下载）。", flush=True)
    folder.mkdir(parents=True, exist_ok=True)
    stage = staging_directory(model, folder, create=True)
    leftovers = {name: verified_publish_leftover(folder / name, expected[name]) for name in missing}
    to_fetch = [name for name in missing if not leftovers[name] and not verify_file(stage / name, expected[name])]
    if to_fetch:
        partial = dict(model, include=to_fetch, exclude=[])
        subprocess.run(command(cli, partial, stage, workers), check=True)
    if missing:
        for name in missing:
            path = leftovers[name] or stage / name
            no_link_parents(path.parent)
            if path.is_symlink():
                raise ValueError('客户端下载结果不能是符号链接：' + name)
            verify_file(path, expected[name], strict=True)
    # 完整下载验证成功才组装。独占创建，绝不覆盖已有文件。
    for name in expected:
        if name in present:
            continue
        dest = folder / name
        no_link_parents(dest.parent)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if name in reuse:
            source = reuse[name]
            verify_file(source, expected[name], strict=True)
            if name in local_reuse:
                no_link_parents(source.parent)
                if source.is_symlink():
                    raise ValueError('复用来源已变成链接，停止安装：' + str(source))
                publish_verified(source, dest, expected[name])
            else:
                try:
                    dest.symlink_to(os.path.relpath(source, dest.parent))
                except OSError as exc:
                    if exc.errno in {errno.ENOTSUP, errno.EOPNOTSUPP, 45}:
                        raise ValueError('此磁盘不支持 H3 去重需要的符号链接；源文件已保留，不以复制冒充节省空间。') from exc
                    raise
        else:
            publish_verified(leftovers[name] or stage / name, dest, expected[name], disposable=True)
    inventory = []
    for name, spec in expected.items():
        path = folder / name
        verify_file(path, spec, strict=True)
        # 保留兼容旧文件时，记录其实际身份，不把新版摘要写给旧权重。
        actual_size = path.stat().st_size
        actual_hash = digest_file(path) if spec.get('compatible_versions') else spec['sha256']
        entry = {'path': name, 'bytes': actual_size, 'sha256': actual_hash}
        if path.is_symlink():
            entry.update({'symlink': os.readlink(path), 'resolved_source': str(path.resolve(strict=True))})
        inventory.append(entry)
    # 自有暂存文件同盘移动；仅成功后清理有匹配标记的暂存目录。
    staging_directory(model, folder)
    shutil.rmtree(stage)
    return inventory


def reuse_estimate(selected, plans, sources, models, verify_sources=True):
    """兼容旧调用；新增空间来自逐文件下载计划，而不是整包体积。"""
    return sum(row['additional'] for row in download_preflight(plans, sources, models, verify_sources))


def adapt_local_weights(model, folder, verify_only=False, source_overrides=None):
    """仅改已核验 safetensors 的键名前缀；原始张量字节与精度完全保留。"""
    results = []
    specs = verified_specs(model)
    no_link_parents(folder)
    for rule in model.get('local_adaptations', []):
        if rule.get('kind') != 'safetensors_prefix' or rule.get('prefix') != 'mtp.':
            raise ValueError('不支持的本地权重适配规则')
        source = (source_overrides or {}).get(rule['source']) or target_path(folder, rule['source'])
        target = target_path(folder, rule['target'])
        if source == target or rule['source'] not in specs:
            raise ValueError('适配源必须是独立、已核查的原文件')
        no_link_parents(source.parent)
        no_link_parents(target.parent)
        if source.is_symlink():
            raise ValueError('权重适配不跟随来源链接')
        verify_file(source, specs[rule['source']], strict=True)
        with source.open('rb') as stream:
            raw = stream.read(8)
            if len(raw) != 8:
                raise ValueError('权重头部不完整')
            length = struct.unpack('<Q', raw)[0]
            if length < 2 or length > 16 * 1024 * 1024:
                raise ValueError('权重头部长度异常')
            header = json.loads(stream.read(length))
            payload_size = source.stat().st_size - 8 - length
            tensors = {k: v for k, v in header.items() if k != '__metadata__'}
            if len(tensors) != rule.get('tensor_count'):
                raise ValueError('MTP 张量数量不匹配')
            end = 0
            for key, value in sorted(tensors.items(), key=lambda pair: pair[1]['data_offsets'][0]):
                start, stop = value['data_offsets']
                shape = value['shape']
                size = 2
                for dim in shape:
                    if not isinstance(dim, int) or dim < 0:
                        raise ValueError('MTP 张量形状无效')
                    size *= dim
                if key.startswith('mtp.') or value['dtype'] != 'BF16' or start != end or stop - start != size:
                    raise ValueError('MTP 必须是连续、无前缀的 BF16 张量')
                end = stop
            if end != payload_size:
                raise ValueError('MTP 张量数据范围与文件不符')
            adapted = {('mtp.' + key if key != '__metadata__' else key): value for key, value in header.items()}
            encoded = json.dumps(adapted, ensure_ascii=False, separators=(',', ':')).encode()
            encoded += b' ' * ((-len(encoded)) % 8)
            prefix = struct.pack('<Q', len(encoded)) + encoded
            expected_hash = hashlib.sha256(prefix)
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                expected_hash.update(block)
            output_spec = {'bytes': len(prefix) + payload_size, 'sha256': expected_hash.hexdigest()}
            declared = model.get('generated_expected_files', {}).get(rule['target'], {})
            if declared and declared.get('bytes') != output_spec['bytes']:
                raise ValueError('兼容权重大小与核查记录不符')
            temporary = verified_adaptation_temporary(target, output_spec)
            if (target.exists() or target.is_symlink()) and not (temporary and owned_publication_reservation(target, temporary)):
                if target.is_symlink():
                    raise ValueError('兼容权重目标不能是链接')
                verify_file(target, output_spec, strict=True)
            elif not verify_only:
                target.parent.mkdir(parents=True, exist_ok=True)
                # 只接受该目标目录内本工具格式的完整临时文件；哈希不符的不动。
                if temporary is None:
                    temporary = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.tmp')
                    stream.seek(8 + length)
                    with temporary.open('xb') as output:
                        output.write(prefix)
                        shutil.copyfileobj(stream, output, 8 * 1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                # 失败保留完整源及临时文件；仅成功后清理本次已验证临时文件。
                publish_verified(temporary, target, output_spec, disposable=True)
                if temporary.exists():
                    temporary.unlink()
            results.append(dict(output_spec, path=rule['target'], source=rule['source'],
                                source_sha256=specs[rule['source']]['sha256'], adaptation='仅添加 mtp. 键名前缀；张量数据不变'))
    return results


def save_manifest(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def discover_cli(allow_uv=False):
    """离线预览不启动外部命令；真实下载可查询 uv 的全局工具目录。"""
    found = shutil.which('ms-hub')
    if found:
        return Path(found)
    if allow_uv and shutil.which('uv'):
        try:
            result = subprocess.run(['uv', 'tool', 'dir', '--bin'], check=True, capture_output=True, text=True)
            folder = result.stdout.strip()
            candidate = Path(folder) / 'ms-hub'
            if folder and candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        except (OSError, subprocess.CalledProcessError):
            pass
    legacy = ROOT / '.venv-download/bin/ms-hub'
    if legacy.is_file() and os.access(legacy, os.X_OK):
        return legacy
    return Path('ms-hub')


GROUPS = (('llm', '大模型'), ('audio', '音频'), ('image_video', '图像/视频'))


def visible_models(models):
    """仅列出可选择的整套模型，序号在列表与交互选择中一致。"""
    available = [m for m in models if m.get('enabled', True) and m.get('repo_id') and not m.get('component', False)]
    return [m for group, _ in GROUPS for m in available if m.get('group', 'llm') == group]


def bundle_size(selected, models):
    """汇总去重后的组件大小；未知大小不当作零。"""
    expanded = expand_dependencies(selected, models)
    known = sum((0 if m.get('final_only_adaptation') else float(m.get('disk_gb') or 0)) + sum(x['bytes'] for x in m.get('generated_expected_files', {}).values()) / 10**9 for m in expanded)
    unknown = any(m.get('disk_gb') is None for m in expanded)
    if unknown:
        return f'至少 {known:.2f} GB，部分未知' if known else '大小未知'
    return f'约 {known:.2f} GB'


def bundle_target(base, model):
    return target_path(base, model.get('bundle_dir') or model.get('subdir') or model['repo_id'])


def print_menu(models, base, details=False):
    """默认一行一套，技术信息仅在明确要求时展示。"""
    choices = visible_models(models)
    print('模型根目录：' + str(base))
    print('大小含必要组件；所有模型均需手动选择。')
    for group, title in GROUPS:
        print('\n' + title)
        entries = [(i, m) for i, m in enumerate(choices, 1) if m.get('group', 'llm') == group]
        if not entries:
            print('  暂无可选模型')
        for i, model in entries:
            print(f"  {i}. {model.get('short_name', model.get('name', model['id']))} | {bundle_size([model], models)}")
            if details:
                print('     最终目录：' + str(bundle_target(base, model)))
                print(f"     id：{model['id']} | 精度：{model.get('precision', '未知')} | {readiness_label(model)}")
                for component in expand_dependencies([model], models):
                    print(f"     组件：{component['id']} | {component['repo_id']} -> {target_path(base, component.get('subdir') or component['repo_id'])}")
                print('     ' + model.get('notes', ''))
    print('\n输入编号可多选；高级命令 models --details 可查看技术详情。')


def main(argv=None):
    p = ChineseArgumentParser(description='仅下载模型；--list / --dry-run 不安装、不联网、不写文件。', epilog='目录提示：在工具包根目录运行 python3 scripts/download_models.py --list；若已进入 scripts 目录，请运行 python3 download_models.py --list。')
    p.add_argument('--catalog', metavar='清单文件', type=Path, default=ROOT / 'catalog.json', help='模型清单文件，默认使用工具包根目录下的 catalog.json')
    paths = p.add_mutually_exclusive_group()
    paths.add_argument('--models-dir', metavar='模型根目录', dest='dest', type=str, help='独立共享模型根目录，默认 ~/AI/models；不写入应用或工具包配置')
    paths.add_argument('--dest', metavar='模型根目录', type=str, help='兼容旧版：与 --models-dir 等价，不可同时指定')
    p.add_argument('--choose-only', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--select', metavar='序号或模型ID', help='清单中的模型 id，逗号分隔；不默认下载全部')
    p.add_argument('--repo', metavar='所有者/仓库名', help='自定义 ModelScope owner/name，与 --select 互斥')
    p.add_argument('--include', metavar='文件模式', action='append', default=[], help='仅 --repo：可重复指定的文件通配模式')
    p.add_argument('--exclude', metavar='排除模式', action='append', default=[], help='仅 --repo：可重复指定的排除通配模式')
    p.add_argument('--revision', metavar='版本标识', default='master', help='仅用于 --repo；可使用固定提交标识')
    p.add_argument('--list', action='store_true', help='离线列出模型与说明，不下载、不写文件')
    p.add_argument('--dry-run', action='store_true', help='仅预览整套下载计划，不联网、不写文件')
    p.add_argument('--details', action='store_true', help='展开组件、精度、许可和下载命令等技术详情')
    p.add_argument('--yes', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--accept-licenses', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--reuse-from', type=Path, metavar='已有H3目录', help='读取核验已有H3权重，仅下载差异并以符号链接组装；不移动、不修改源文件')
    p.add_argument('--reuse-mtp-source', type=Path, metavar='旧MTP目录', help='仅核验指定旧 mtp 目录及其匹配暂存目录中的原件；允许旧目录名有首尾空白，不移动、不删除旧文件')
    p.add_argument('--sha256', action='store_true', help='下载后额外逐文件计算 SHA256，可能耗时很长')
    p.add_argument('--workers', metavar='并行数', type=int, default=4, help='并行下载任务数，范围 1–32，默认 4')
    p.add_argument('--cli', type=Path, metavar='客户端路径', help='覆盖自动发现的 ms-hub；默认依次查找 PATH、uv 工具目录、旧版虚拟环境')
    a = p.parse_args(argv)
    if a.dest is not None:
        a.dest = validate_model_root(a.dest)
    if not 1 <= a.workers <= 32:
        p.error('--workers 范围 1..32')
    if a.select and a.repo:
        p.error('--select 和 --repo 不能同时使用')
    if (a.include or a.exclude) and not a.repo:
        p.error('--include / --exclude 仅配合 --repo；清单模式请在 catalog 内配置')
    if any(not value or value.startswith('-') or '\x00' in value for value in a.include + a.exclude):
        p.error('include/exclude 模式无效')
    models = [] if a.repo else load_catalog(a.catalog)
    if a.list:
        base = (a.dest or Path('~/AI/models')).expanduser().resolve()
        print_menu(models, base, a.details)
        return 0
    if a.repo:
        repo = safe_repo(a.repo)
        selected = [{'id': repo.replace('/', '--'), 'name': repo, 'repo_id': repo, 'revision': a.revision, 'precision': '用户指定，请自行核对', 'license': '用户自行核对', 'include': a.include, 'exclude': a.exclude, 'source_url': 'https://modelscope.cn/models/' + repo}]
    else:
        choices = a.select
        interactive = not choices
        if interactive:
            if not sys.stdin.isatty() and not a.choose_only:
                p.error('非交互模式必须指定 --select 或 --repo；查看 --list')
            # 选择专用模式把提示放到 stderr，stdout 只返回 ID，不创建文件。
            from contextlib import redirect_stdout
            with redirect_stdout(sys.stderr if a.choose_only else sys.stdout):
                print_menu(models, (a.dest or Path('~/AI/models')).expanduser().resolve(), a.details)
        while True:
            if interactive:
                try:
                    with redirect_stdout(sys.stderr if a.choose_only else sys.stdout):
                        choices = input('输入编号（逗号或空格分隔；0 或空白取消）：').strip()
                except EOFError:
                    return 0
                if not choices or choices in ('0', 'q'):
                    return 0
            selected = []
            try:
                for key in choices.replace(',', ' ').split():
                    found = next((m for i, m in enumerate(visible_models(models), 1) if key in (str(i), m['id'])), None)
                    if not found:
                        raise ValueError('未知模型选择：' + key)
                    if not found.get('enabled', True) or not found.get('repo_id'):
                        raise ValueError('模型未启用，不能下载：' + key)
                    if found not in selected:
                        selected.append(found)
                if not selected:
                    raise ValueError('请选择至少一个模型编号')
                break
            except ValueError as exc:
                if not interactive:
                    raise
                print(str(exc) + '，请重新选择。', file=sys.stderr)
    if a.reuse_mtp_source and (a.repo or not any(m.get('final_only_adaptation') for m in expand_dependencies(selected, models))):
        raise ValueError('--reuse-mtp-source 仅用于含 MTP 的模型')
    if a.choose_only:
        if not a.repo:
            expand_dependencies(selected, models)
        print(','.join(m['id'] for m in selected))
        return 0
    requested_models = selected[:]
    if not a.repo:
        selected = expand_dependencies(selected, models)
    if a.dest is None and sys.stdin.isatty():
        response = input('模型根目录 [~/AI/models]：').strip()
        raw_base = validate_model_root(response or '~/AI/models')
    else:
        raw_base = validate_model_root(a.dest if a.dest is not None else '~/AI/models')
    base = raw_base.resolve()
    if not a.dry_run:
        no_link_parents(raw_base)
    if a.reuse_from and (a.repo or not any(reuse_family(m) for m in selected)):
        raise ValueError('--reuse-from 仅支持声明了共享家族和核查快照的 H3 模型')
    a.cli = a.cli or discover_cli(allow_uv=not a.dry_run and platform.system() == 'Darwin')
    plans = []
    for m in selected:
        target = target_path(base, m.get('subdir') or m['repo_id'])
        plans.append((m, target, command(a.cli, m, target, a.workers)))
    validate_plan_targets(plans)
    if a.dry_run or a.details:
        print('\n本次下载（已包含必要组件）：')
        for m in requested_models:
            print(f"  {m.get('short_name', m.get('name', m['id']))} | {bundle_size([m], models or selected)} | {bundle_target(base, m)}")
        if any(reuse_family(m) for m in selected):
            print('各版本文件合计：' + bundle_size(requested_models, models or selected) + '（未扣同内容权重）')
        else:
            print('合计：' + bundle_size(requested_models, models or selected) + '（共享组件只计一次）')
    if a.details:
        for m, target, _ in plans:
            print(f"\n{m['id']} -> {target}\n  仓库：{m['repo_id']} | 精度：{m.get('precision', '未知')} | {readiness_label(m)}")
            print(f"  许可：{m.get('license', '请自行核对')} | {m.get('license_url', m.get('source_url', ''))}")
            print('  ' + m.get('notes', ''))
            print('  ' + m.get('license_notice', ''))
    if any(reuse_family(m) for m in selected):
        estimated = reuse_estimate(selected, plans, [], models or selected, verify_sources=False)
        print('普通下载会自动检查当前模型根目录中的 H3 两版及旧默认目录；仅 SHA256 一致才复用，不扫描其他位置。')
        print(f'按核查 SHA 去重后预计新增约 {estimated / 10**9:.2f} GB（已有源文件须实际校验后扣减）')
    if a.dry_run:
        if a.reuse_mtp_source:
            print('旧 MTP 来源：' + repr(str(a.reuse_mtp_source)) + '；只核验精确白名单原件及匹配暂存目录，不修改旧目录。')
        if a.reuse_from:
            print('复用源：' + str(a.reuse_from.expanduser()) + '；实际下载前校验 SHA256，预览不读取大权重。')
        if a.details:
            print(json.dumps({'dry_run': True, 'models_dir': str(base), 'commands': [v[2] for v in plans]}, ensure_ascii=False, indent=2))
        else:
            print('仅预览，未下载；从「管理本地模型 → 下载模型」选择下载。')
        return 0
    if platform.system() != 'Darwin':
        raise ValueError('真实下载仅支持 macOS；其他平台可 --list / --dry-run')
    ancestor = base
    while not ancestor.exists():
        ancestor = ancestor.parent
    free = shutil.disk_usage(ancestor).free
    sources = [a.reuse_from] if a.reuse_from else []
    if any(is_h3_reuse_model(m) for m in selected):
        sources += automatic_reuse_sources(base, models or selected)
    plans = prioritize_existing_h3(plans)
    print('检查已有模型：' + str(base), flush=True)
    print('正在核验已有文件；只补缺失组件，已有文件保持原位。', flush=True)
    preflight = download_preflight(plans, sources, models or selected, base=base, report=True, old_mtp_folder=a.reuse_mtp_source)
    free = shutil.disk_usage(ancestor).free
    estimate = sum(row['additional'] for row in preflight)
    network = sum(row['network'] for row in preflight)
    print(f'可用磁盘 {free / 10**9:.1f} GB；本次下载 {network / 10**9:.2f} GB；本次新增 {estimate / 10**9:.2f} GB', flush=True)
    peak = estimate + max((row.get('peak_additional', row['additional']) - row['additional'] for row in preflight), default=0)
    if peak > estimate:
        print(f'处理期间最多需要新增空间 {peak / 10**9:.2f} GB；仅清理成功的本次暂存，旧来源保留。', flush=True)
    if peak > free:
        raise ValueError('磁盘空间不足以补齐缺失组件；请更换下载目录')
    if (network or any(not m.get('expected_files') for m in selected)) and (not a.cli.is_file() or not os.access(a.cli, os.X_OK)):
        raise ValueError('没有官方下载客户端；请从「管理本地模型 → 下载模型」按提示准备下载客户端')
    base.mkdir(parents=True, exist_ok=True)
    manifest_dir = target_path(base, 'Mac-AI-Manifests')
    manifest_dir.mkdir(exist_ok=True)
    run_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    manifest_path = manifest_dir / (run_id + '.json')
    manifest = {'schema_version': 1, 'started_utc': run_id.split('-')[0], 'status': 'running', 'client': 'modelscope-hub', 'catalog_sha256': None if a.repo else hashlib.sha256(a.catalog.read_bytes()).hexdigest(), 'models': []}
    save_manifest(manifest_path, manifest)
    completed_sources = {}
    try:
        for index, (m, folder, args) in enumerate(plans, 1):
            if a.details:
                print(f"正在下载 {index}/{len(plans)}：{m.get('short_name', m.get('name', m['id']))}", flush=True)
            record = {'id': m['id'], 'repo_id': m['repo_id'], 'requested_revision': m.get('revision') or 'master', 'resolved_commit': None, 'source_url': m.get('source_url'), 'destination': str(folder), 'include': m.get('include', []), 'exclude': m.get('exclude', []), 'status': 'running'}
            manifest['models'].append(record)
            save_manifest(manifest_path, manifest)
            # 参数以列表传递，不经 shell 求值；不导入仓库代码。
            family = reuse_family(m)
            if m.get('final_only_adaptation'):
                record['files'] = download_final_mtp(a.cli, m, folder, a.workers, a.reuse_mtp_source)
            elif m.get('expected_files'):
                model_sources = sources + completed_sources.get(family, [])
                record['files'] = download_verified_bundle(a.cli, m, folder, a.workers, model_sources, models or selected, preflight[index - 1]['local_reuse'])
                completed_sources.setdefault(family, []).append(folder)
            else:
                subprocess.run(args, check=True)
                record['files'] = file_inventory(folder, a.sha256)
            if not record['files']:
                raise ValueError('下载目录无文件，请检查仓库与include过滤条件')
            if not m.get('final_only_adaptation'):
                check_expected_files(m, record['files'], a.sha256)
            if m.get('local_adaptations') and not m.get('final_only_adaptation'):
                record['generated_files'] = adapt_local_weights(m, folder)
            record['status'] = 'downloaded'
            save_manifest(manifest_path, manifest)
        manifest['status'] = 'downloaded'
    except BaseException:
        manifest['status'] = 'incomplete'
        if manifest['models']:
            manifest['models'][-1]['status'] = 'incomplete'
        raise
    finally:
        manifest['finished_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
        save_manifest(manifest_path, manifest)
        print('下载记录：' + str(manifest_path))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n已取消。相同目录再次运行由官方客户端处理续传。', file=sys.stderr)
        sys.exit(130)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'错误：{exc}', file=sys.stderr)
        sys.exit(1)
