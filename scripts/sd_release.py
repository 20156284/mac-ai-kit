#!/usr/bin/env python3
"""动态解析并安装官方 SD.cpp 发行包，绝不执行下载的程序。

安装计划是本次操作的快照，不是固定版本。完整包凭据用于检测被修改或缺失的运行库。
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import stat
import struct
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = 'leejet/stable-diffusion.cpp'
API = 'https://api.github.com/repos/' + REPOSITORY + '/releases/'
DOWNLOAD = 'https://github.com/' + REPOSITORY + '/releases/download/'
RECEIPT = '.mac-ai-kit-release.json'
MAX_ARCHIVE = 2 * 1024**3
MAX_EXPANDED = 4 * 1024**3


def read_json_url(url):
    request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'mac-ai-kit'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get('schema') != 1 or plan.get('repository') != REPOSITORY:
        raise ValueError('不是官方 SD.cpp 发行计划')
    tag, name = plan.get('tag', ''), plan.get('asset_name', '')
    if not isinstance(tag, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', tag):
        raise ValueError('发行 tag 无效')
    if (
        not isinstance(name, str)
        or not re.fullmatch(r'[A-Za-z0-9._+-]+', name)
        or not (name.endswith('.zip') and 'Darwin' in name and 'arm64' in name)
    ):
        raise ValueError('不是 macOS ARM64 ZIP')
    if plan.get('download_url') != DOWNLOAD + tag + '/' + name:
        raise ValueError('下载地址不是官方发行资源')
    if not isinstance(plan.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', plan['sha256']):
        raise ValueError('缺少官方 SHA256 digest；不会降级为未校验下载')
    return plan


def release_plan(release):
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('latest 必须是非草稿、非预发行版本')
    matches = [
        a
        for a in release.get('assets', [])
        if isinstance(a.get('name'), str)
        and 'Darwin' in a['name']
        and 'arm64' in a['name']
        and a['name'].endswith('.zip')
    ]
    if len(matches) != 1:
        raise ValueError('官方发行必须有且只有一个 macOS ARM64 ZIP')
    asset = matches[0]
    digest = asset.get('digest') or ''
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('官方资源缺少 SHA256 digest；拒绝安装')
    return validate_plan(
        dict(
            schema=1,
            repository=REPOSITORY,
            tag=release.get('tag_name'),
            asset_name=asset['name'],
            download_url=asset.get('browser_download_url'),
            sha256=digest[7:],
        )
    )


def resolve_latest():
    return release_plan(read_json_url(API + 'latest'))


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def plain_path(path):
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    if any(c in str(path) for c in '\n\r\x00') or path == Path(path.anchor):
        raise ValueError('无效安装路径')
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('安装路径含符号链接: ' + str(part))
    return path


def safe_extract(archive, destination):
    with zipfile.ZipFile(archive) as package:
        infos = package.infolist()
        if len(infos) > 20000 or sum(i.file_size for i in infos) > MAX_EXPANDED:
            raise ValueError('发行包体积或文件数超限')
        seen = set()
        for item in infos:
            name = item.filename
            path = PurePosixPath(name)
            mode = item.external_attr >> 16
            normalized = str(path).casefold()
            if (
                not name
                or '\\' in name
                or '\x00' in name
                or '\n' in name
                or '\r' in name
                or path.is_absolute()
                or '..' in path.parts
                or normalized in seen
                or any(p in ('', '.') for p in name.rstrip('/').split('/'))
                or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))
            ):
                raise ValueError('发行 ZIP 含不安全或重复路径: ' + name)
            seen.add(normalized)
            if path.name == RECEIPT:
                raise ValueError('发行 ZIP 不得包含本地受管凭据')
        for item in infos:
            target = destination.joinpath(*PurePosixPath(item.filename).parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as source, open(target, 'xb') as sink:
                    shutil.copyfileobj(source, sink)
                target.chmod(
                    0o755 if (item.external_attr >> 16) & 0o111 or target.name in ('sd-server', 'sd-cli') else 0o644
                )


def macho_minimum(data):
    """静态读取普通或通用 Mach-O 的 ARM64 最低 macOS，不调用程序或 otool。"""
    if len(data) < 8:
        raise ValueError('不是有效 Mach-O')
    if data[:4] in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf', b'\xbe\xba\xfe\xca', b'\xbf\xba\xfe\xca'):
        endian = '>' if data[0] == 0xCA else '<'
        is64 = data[:4] in (b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca')
        count = struct.unpack_from(endian + 'I', data, 4)[0]
        width = 32 if is64 else 20
        if not 1 <= count <= 32 or 8 + count * width > len(data):
            raise ValueError('损坏的 Fat Mach-O')
        slices = []
        for index in range(count):
            offset = 8 + index * width
            cpu = struct.unpack_from(endian + 'I', data, offset)[0]
            start, length = struct.unpack_from(endian + ('QQ' if is64 else 'II'), data, offset + 8)
            if start < 8 + count * width or length < 32 or start + length > len(data):
                raise ValueError('Fat Mach-O 切片越界')
            if cpu == 0x100000C:
                slices.append(macho_minimum(data[start : start + length]))
        if len(slices) != 1:
            raise ValueError('需要唯一 ARM64 Mach-O 切片')
        return slices[0]
    if data[:4] != b'\xcf\xfa\xed\xfe' or len(data) < 32:
        raise ValueError('需要 64 位 ARM64 Mach-O，不接受脚本或其他架构')
    _, cpu, _, _, count, size, _, _ = struct.unpack_from('<8I', data)
    if cpu != 0x100000C or size > len(data) - 32 or count > size // 8:
        raise ValueError('Mach-O 架构或命令长度无效')
    cursor, minimums = 32, []
    for _ in range(count):
        if cursor + 8 > 32 + size:
            raise ValueError('Mach-O load command 越界')
        cmd, length = struct.unpack_from('<II', data, cursor)
        if length < 8 or length % 4 or cursor + length > 32 + size:
            raise ValueError('Mach-O load command 长度无效')
        if cmd == 0x32:
            if length < 24:
                raise ValueError('LC_BUILD_VERSION 长度无效')
            target, version = struct.unpack_from('<II', data, cursor + 8)
            if target != 1:
                raise ValueError('不是 macOS Mach-O')
            minimums.append(version)
        elif cmd == 0x24:
            if length < 16:
                raise ValueError('LC_VERSION_MIN_MACOSX 长度无效')
            minimums.append(struct.unpack_from('<I', data, cursor + 8)[0])
        cursor += length
    if cursor != 32 + size or not minimums:
        raise ValueError('Mach-O 缺少最低 macOS 声明')
    version = max(minimums)
    return (version >> 16, (version >> 8) & 255, version & 255)


def inspect_runtime(folder):
    files = {}
    minimums = []
    binaries = []
    for path in sorted(folder.rglob('*')):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError('受管包含非普通文件')
        if path.is_file() and path != folder / RECEIPT:
            files[path.relative_to(folder).as_posix()] = sha256(path)
            if path.name == 'sd-server':
                binaries.append(path)
            if path.name in ('sd-server', 'sd-cli') or path.suffix == '.dylib':
                minimums.append(macho_minimum(path.read_bytes()))
    if len(binaries) != 1 or not os.access(binaries[0], os.X_OK):
        raise ValueError('需要且只能有一个可执行 sd-server')
    required = (b'--model-dir', b'/sdcpp/v1/models/load', b'/sdcpp/v1/models/status')
    if not all(token in binaries[0].read_bytes() for token in required):
        raise ValueError('发行 sd-server 缺少 --model-dir 或 load/status 模型管理 API；不会回退到旧版')
    return dict(
        binary_relative=binaries[0].relative_to(folder).as_posix(),
        files=files,
        minimum_macos='.'.join(map(str, max(minimums))),
        model_directory_capable=True,
    )


def validate_installed(folder):
    folder = plain_path(folder)
    if not folder.is_dir():
        raise ValueError('受管 SD.cpp 目录不存在')
    marker = folder / RECEIPT
    if marker.is_symlink() or not marker.is_file():
        raise ValueError('缺少受管 SD.cpp 安装记录')
    metadata = validate_plan(json.loads(marker.read_text()))
    actual = inspect_runtime(folder)
    for key, value in actual.items():
        if metadata.get(key) != value:
            raise ValueError('受管 SD.cpp 文件或能力核验失败: ' + key)
    return metadata


def download(plan, target):
    with urllib.request.urlopen(plan['download_url'], timeout=120) as response, open(target, 'xb') as output:
        total = 0
        while True:
            data = response.read(1024 * 1024)
            if not data:
                break
            total += len(data)
            if total > MAX_ARCHIVE:
                raise ValueError('发行下载大小超限')
            output.write(data)
    if sha256(target) != plan['sha256']:
        raise ValueError('SHA256 校验不匹配')


def wrapper_text(binary):
    return '#!/usr/bin/env bash\nexec ' + shlex.quote(str(binary)) + ' "$@"\n'


def legacy_wrapper_text(binary):
    # 复现 Bash printf %q 对普通可打印路径的反斜杠转义。
    raw = str(binary)
    if any(ord(c) < 32 or ord(c) > 126 for c in raw):
        return None
    escaped = ''.join(c if c.isalnum() or c in '_@%+=:,./-' else '\\' + c for c in raw)
    return '#!/usr/bin/env bash\nexec ' + escaped + ' "$@"\n'


def entry_snapshot(entry):
    if entry.is_symlink():
        return ('symlink', os.readlink(entry))
    if entry.is_file():
        return ('file', entry.read_bytes())
    if entry.exists():
        return ('other', None)
    return ('missing', None)


def owned_entry(root, snapshot, current):
    if snapshot[0] == 'missing':
        return True
    if snapshot[0] != 'file':
        return False
    if current and snapshot[1] == wrapper_text(root / 'apps/sd.cpp' / current['binary_relative']).encode():
        return True
    for folder in (root / 'apps').glob('sd.cpp-*'):
        if folder.is_symlink() or not folder.is_dir():
            continue
        receipt = folder / '.lean-ai-release'
        if receipt.is_symlink() or not receipt.is_file():
            continue
        binaries = list(folder.rglob('sd-server'))
        if len(binaries) != 1 or binaries[0].is_symlink() or not binaries[0].is_file():
            continue
        try:
            plain_path(binaries[0])
        except ValueError:
            continue
        expected = legacy_wrapper_text(binaries[0])
        if expected is None or snapshot[1] != expected.encode():
            continue
        # 旧凭据没有完整文件清单；必须向官方 tag 元数据核验摘要，
        # 且旧包装脚本逐字一致，才允许更新入口。
        match = re.fullmatch(
            r'([0-9a-f]{64})  (https://github.com/leejet/stable-diffusion.cpp/releases/download/([^/]+)/[^\s]+)\n',
            receipt.read_text(),
        )
        if not match:
            continue
        try:
            old = release_plan(read_json_url(API + 'tags/' + urllib.parse.quote(match[3], safe='')))
        except (OSError, ValueError):
            continue
        if old['sha256'] == match[1] and old['download_url'] == match[2]:
            return True
    return False


def assert_stopped(root):
    if platform.system() != 'Darwin' or platform.machine() not in ('arm64', 'aarch64'):
        raise ValueError('SD.cpp 发行安装仅支持 macOS ARM64')
    suffix = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    label = f'gui/{os.getuid()}/org.mac-ai-kit.native.sd-qwen.{suffix}'
    result = subprocess.run(['/bin/launchctl', 'print', label], capture_output=True, text=True)
    if result.returncode == 0:
        raise ValueError('SD.cpp 原生服务仍已加载，请先停止服务再升级')
    result = subprocess.run(['/bin/ps', '-axo', 'command='], capture_output=True, text=True)
    if result.returncode:
        raise ValueError('无法确认 SD.cpp 进程已停止')
    if any(str(root / 'apps/sd.cpp') in line and 'sd-server' in line for line in result.stdout.splitlines()):
        raise ValueError('SD.cpp 进程仍运行，请先停止再升级')


def check_host(minimum):
    if platform.system() != 'Darwin' or platform.machine() not in ('arm64', 'aarch64'):
        raise ValueError('SD.cpp 发行安装仅支持 macOS ARM64')
    current = tuple(int(x) for x in platform.mac_ver()[0].split('.'))
    needed = tuple(int(x) for x in minimum.split('.'))
    if (current + (0, 0, 0))[:3] < needed:
        raise ValueError('Mach-O 声明最低 macOS ' + minimum + '，当前系统不兼容')


def install(root, plan):
    plan = validate_plan(plan)
    root = plain_path(root)
    apps, bindir = plain_path(root / 'apps'), plain_path(root / 'bin')
    target, backup = plain_path(apps / 'sd.cpp'), plain_path(apps / '.sd.cpp.previous')
    current = validate_installed(target) if target.exists() else None
    prior_backup = validate_installed(backup) if backup.exists() else None
    entry = bindir / 'sd-server'
    initial = entry_snapshot(entry)
    managed = owned_entry(root, initial, current)

    def publish_entry(metadata):
        if not managed:
            return
        plain_path(bindir)
        bindir.mkdir(parents=True, exist_ok=True)
        if entry_snapshot(entry) != initial:
            raise ValueError('受管入口在安装过程中变化，未覆盖入口')
        fd, name = tempfile.mkstemp(prefix='.sd-server-', dir=bindir)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(wrapper_text(target / metadata['binary_relative']))
            os.chmod(name, 0o755)
            if entry_snapshot(entry) != initial:
                raise ValueError('受管入口在安装过程中变化，未覆盖入口')
            os.replace(name, entry)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    if current and all(current[k] == plan[k] for k in ('tag', 'sha256', 'download_url')):
        check_host(current['minimum_macos'])
        metadata = current
        publish_entry(metadata)
    else:
        assert_stopped(root)
        apps.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.sd.cpp-stage-', dir=apps) as temp:
            stage = Path(temp)
            archive, payload = stage / 'release.zip', stage / 'payload'
            payload.mkdir()
            download(plan, archive)
            safe_extract(archive, payload)
            metadata = dict(plan, **inspect_runtime(payload))
            check_host(metadata['minimum_macos'])
            (payload / RECEIPT).write_text(json.dumps(metadata, indent=2) + '\n')
            validate_installed(payload)
            assert_stopped(root)
            plain_path(apps)
            plain_path(bindir)
            plain_path(target)
            if entry_snapshot(entry) != initial:
                raise ValueError('受管入口在安装过程中变化，未发布新包')
            if (validate_installed(target) if target.exists() else None) != current:
                raise ValueError('受管目录在安装过程中变化')
            if (validate_installed(backup) if backup.exists() else None) != prior_backup:
                raise ValueError('受管备份在安装过程中变化')
            if prior_backup:
                shutil.rmtree(backup)
            if current:
                target.rename(backup)
            try:
                payload.rename(target)
                publish_entry(metadata)
            except BaseException:
                if target.exists():
                    if validate_installed(target) != metadata:
                        raise ValueError('发布失败且新目录被修改，请手动检查；已保留旧版备份')
                    target.rename(payload)
                if current:
                    backup.rename(target)
                raise
    return dict(metadata, installation_path=str(target), entry_updated=managed)


class ChineseParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs['add_help'] = False
        super().__init__(*args, **kwargs)
        self.add_argument('-h', '--help', action='help', help='显示本帮助并退出')
        self._positionals.title = '命令'
        self._optionals.title = '选项'

    def format_usage(self):
        return super().format_usage().replace('usage:', '用法:')

    def format_help(self):
        return super().format_help().replace('usage:', '用法:')

    def error(self, message):
        self.print_usage()
        self.exit(2, '参数无效，请使用 --help 查看用法。\n')


def main():
    parser = ChineseParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    resolve = commands.add_parser('resolve', help='解析官方最新发行')
    resolve.add_argument('--out', required=True, help='本次官方发行计划 JSON 文件')
    installer = commands.add_parser('install', help='校验并安装本次发行计划')
    installer.add_argument('--root', required=True, help='AI 安装根目录')
    installer.add_argument('--plan', required=True, help='已经解析的本次发行计划')
    args = parser.parse_args()
    try:
        if args.command == 'resolve':
            plan = resolve_latest()
            Path(args.out).write_text(json.dumps(plan, indent=2) + '\n')
            print('SD.cpp：已选择官方最新发行 ' + plan['tag'])
        else:
            result = install(args.root, json.loads(Path(args.plan).read_text()))
            print('已安装：' + result['installation_path'] + '（' + result['tag'] + '）')
            if not result['entry_updated']:
                print('已保留外部或未知 sd-server 入口；原生服务直接使用受管程序。')
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, 'SD.cpp 安装未完成：' + str(exc) + '\n')


if __name__ == '__main__':
    main()
