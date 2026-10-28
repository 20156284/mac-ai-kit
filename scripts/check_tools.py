#!/usr/bin/env python3
"""只读检查文件元数据与进程名称，绝不执行组件代码。"""
import argparse
import os
import json
import re
from pathlib import Path
import shutil
import stat
import subprocess
import sys

# 不匹配通用 python、node 或 server 名称，避免误认其他进程。
COMPONENTS = (
    ('MLX-Serve App', (), ('mlx-serve', 'MLX-Serve', 'MLX Core'), ('mlx-serve.json', 'mlx-serve.yaml')),
    ('llama.cpp', ('llama-server', 'llama-cli'), ('llama-server', 'llama-cli'), ('llama-server.json', 'llama-server.yaml')),
    ('llama-swap', ('llama-swap',), ('llama-swap',), ('llama-swap.yaml', 'llama-swap.yml')),
    ('sd.cpp', ('sd-server', 'sd-cli'), ('sd-server', 'sd-cli'), ('sd-server.json', 'sd-server.yaml')),
    ('audio.cpp', ('audiocpp_server', 'audiocpp'), ('audiocpp_server', 'audiocpp'), ('audio-server.json', 'qwen-asr-server.json', 'yue2-server.json')),
    ('Hermes', ('hermes',), ('hermes', 'hermes-agent'), ('hermes/config.yaml',)),
    ('Remotion（可选）', ('remotion',), (), ('remotion/remotion.config.ts', 'remotion/remotion.config.js', 'remotion.config.ts', 'remotion.config.js')),
)


def safe_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or path == Path('/') or '..' in path.parts or any(ord(c) < 32 or ord(c) == 127 for c in str(path)):
        raise ValueError('目录必须为绝对路径，不能是根目录、含控制字符或 ..')
    return path


def metadata(path, executable=False, directory=False):
    """不遍历任意符号链接父目录，不读取文件内容。"""
    try:
        for parent in reversed(path.parents):
            info = parent.lstat()
            if stat.S_ISLNK(info.st_mode) and str(parent) not in ('/tmp', '/var', '/etc'):
                return 'unknown'
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                return 'unknown'
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            return 'unknown'
        correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not correct_type or (executable and not os.access(path, os.X_OK)):
            return 'unknown'
        return 'present'
    except FileNotFoundError:
        return 'missing'
    except (OSError, RuntimeError):
        return 'unknown'


def read_processes():
    # 使用系统 ps 的绝对路径，不经 PATH，不收集参数或环境变量。
    ps = '/bin/ps' if Path('/bin/ps').is_file() else '/usr/bin/ps'
    try:
        result = subprocess.run([ps, '-axo', 'pid=,comm='], capture_output=True,
                                text=True, timeout=5, check=False)
        if result.returncode != 0:
            return None
        return parse_processes(result.stdout)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None


def parse_processes(text):
    processes = []
    for line in text.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and fields[0].isdigit() and int(fields[0]) > 0:
            processes.append((int(fields[0]), Path(fields[1]).name))
    return processes


def probe(paths, **kwargs):
    return [(path, metadata(path, **kwargs)) for path in dict.fromkeys(paths)]


def state_text(probes, missing):
    count = sum(state == 'present' for _, state in probes)
    if count:
        return '已发现'
    if any(state == 'unknown' for _, state in probes):
        return '未核实'
    return missing


def remotion_configs(home):
    """最多检查 128 个直属条目；不读取配置，也不跟随项目目录链接。"""
    base = home / 'Library/Application Support/RemotionRemote'
    status = metadata(base, directory=True)
    if status != 'present':
        return [(base, status)]
    found = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(base, flags)
        try:
            with os.scandir(descriptor) as entries:
                for index, entry in enumerate(entries):
                    if index >= 128:
                        found.append((base, 'unknown'))
                        break
                    if not re.fullmatch(r'[0-9a-f]{16}', entry.name):
                        continue
                    path = base / entry.name / 'config.json'
                    try:
                        child = os.open(entry.name, flags, dir_fd=descriptor)
                        try:
                            info = os.stat('config.json', dir_fd=child, follow_symlinks=False)
                            state = 'present' if stat.S_ISREG(info.st_mode) else 'unknown'
                        finally:
                            os.close(child)
                    except FileNotFoundError:
                        state = 'missing'
                    except OSError:
                        state = 'unknown'
                    found.append((path, state))
        finally:
            os.close(descriptor)
    except OSError:
        found.append((base, 'unknown'))
    return found


def inventory(root, home, processes):
    rows = []
    for name, commands, aliases, configs in COMPONENTS:
        binaries = []
        for command in commands:
            located = shutil.which(command)
            if located:
                binaries.append(Path(located).absolute())
            binaries.extend((root / 'bin' / command, home / '.local/bin' / command, home / 'bin' / command))
        installed = probe(binaries, executable=True)
        app_state = None
        if name.startswith('MLX'):
            apps = [base / app for base in (home / 'Applications', Path('/Applications'))
                    for app in ('MLX-Serve.app', 'MLX Core.app')]
            app_state = state_text(probe(apps, directory=True), '未发现')
            installed += probe(apps, directory=True)
        config_paths = [root / 'config' / p for p in configs] + [root / p for p in configs]
        if name == 'llama-swap':
            config_paths.append(root / 'services/llama-swap/config.json')
        if name.startswith('MLX'):
            config_paths += [home / 'Library/Preferences/com.dalcu.mlx-core.plist', home / '.mlx-serve/config.json']
        if name == 'Hermes':
            hermes_home = safe_path(os.environ.get('HERMES_HOME', str(home / '.hermes')))
            config_paths.append(hermes_home / 'config.yaml')
        configured = probe(config_paths)
        if not aliases:
            configured += remotion_configs(home)
        pids = sorted({pid for pid, comm in (processes or []) if comm in aliases})
        runtime = ('发现 PID ' + ','.join(map(str, pids)) if pids else
                   '未核实' if processes is None else
                   '未核实' if not aliases else
                   '未发现')
        install_text = state_text(installed, '未发现')
        if app_state is not None:
            install_text = 'App：' + app_state
        rows.append({'name': name, 'installed': install_text,
                     'configured': state_text(configured, '未发现'),
                     'process': runtime, 'probes': installed + configured})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description='只读检查工具安装、配置文件与当前进程；不启动或停止任何工具。')
    parser.add_argument('--root', default=None, help='工具安装根目录，默认 ~/AI；不存在也不会创建')
    parser.add_argument('--models-dir', default=None, help='主菜单选择的模型目录，只读显示，不改变工具安装目录')
    parser.add_argument('--details', action='store_true', help='显示已发现或未核实的路径，不读取配置内容')
    args = parser.parse_args(argv)
    try:
        home = safe_path(os.environ.get('HOME', str(Path.home())))
        root = safe_path(args.root if args.root is not None else str(home / 'AI'))
        models = safe_path(args.models_dir) if args.models_dir else home / 'AI/models'
        rows = inventory(root, home, read_processes())
    except (ValueError, OSError, RuntimeError) as error:
        print('检查失败：' + str(error), file=sys.stderr)
        return 2
    print('工具状态（只读）\n仅查已知路径与进程名：文件存在不代表可用；PID 不代表 API 健康；解释器及改名进程可能漏检。')
    print('模型目录：' + str(models))
    print('llama-swap 启停、配置校验与登录启动：主菜单 → llama-swap（可选），或 bash start.sh services status。')
    for row in rows:
        print('\n' + row['name'])
        print('  安装：' + row['installed'])
        print('  配置：' + row['configured'])
        print('  进程：' + row['process'])
        if args.details:
            for path, state in row['probes']:
                if state != 'missing':
                    print('  路径：' + json.dumps(str(path), ensure_ascii=False) + ' [' + state + ']')
    print('\n未启动或停止工具；未读取配置内容。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
