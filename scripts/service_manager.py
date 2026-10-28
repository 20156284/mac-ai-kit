#!/usr/bin/env python3
"""显式管理用户级 llama-swap；不安装、不推理、不使用 sudo。"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from path_validation import validate_model_root


class ServiceError(ValueError):
    pass


def checked_path(value):
    p = validate_model_root(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in str(p)):
        raise ServiceError('路径不能含控制字符')
    return p


def executable(root, name):
    local = root / 'bin' / name
    candidate = local if local.is_file() and os.access(local, os.X_OK) else shutil.which(name)
    return str(Path(candidate).absolute()) if candidate else None


def port_number(value):
    n = int(value)
    if not 1024 <= n <= 65535:
        raise ServiceError('端口必须在 1024–65535 之间')
    return n


def proxy_bind(value):
    if value not in ('127.0.0.1', '0.0.0.0'):
        raise ServiceError('llama-swap 监听地址只支持 127.0.0.1 或 0.0.0.0')
    return value


def access_notice(data):
    if data['proxy_bind'] == '0.0.0.0':
        return ('llama-swap 监听所有 IPv4 接口（0.0.0.0），不是仅限局域网的访问控制；'
                '未配置认证或 TLS，可达设备能发起推理并占用资源。仅用于可信局域网，不要映射公网。'
                '不修改防火墙或路由器。')
    return 'llama-swap 仅监听本机 127.0.0.1，其他机器不能访问。'


def choose_proxy_bind():
    print('llama-swap 访问范围：1 可信局域网（0.0.0.0）  2 仅本机（127.0.0.1）')
    choice = input('选择 [1]：').strip() or '1'
    if choice not in ('1', '2'):
        raise ServiceError('无效访问范围')
    value = '0.0.0.0' if choice == '1' else '127.0.0.1'
    print(access_notice({'proxy_bind': value}))
    return value


def available_port(port, host='127.0.0.1'):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
    except OSError:
        raise ServiceError(f'{host}:{port} 已占用或无法绑定；不会停止其他程序，请选择其他端口')


def matches_config(actual, expected, backend):
    return backend == 'gguf' and actual == expected


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ServiceError(f'拒绝覆盖符号链接：{path}')
    tmp = path.with_name(path.name + '.tmp-' + str(os.getpid()))
    try:
        with tmp.open('xb') as out:
            os.chmod(tmp, 0o600)
            out.write(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


class Manager:
    def __init__(self, root, models, home=None):
        self.root = checked_path(root)
        self.models = checked_path(models)
        self.home = Path(home or Path.home())
        self.base = self.root / 'services' / 'llama-swap'
        self.bundle = self.base / 'runtime'
        self.settings = self.base / 'settings.json'
        self.config = self.base / 'config.json'  # JSON 属于 YAML 子集，无需 YAML 依赖。
        suffix = hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
        self.label = 'org.mac-ai-kit.llama-swap.' + suffix
        self.target = f'gui/{os.getuid()}/{self.label}'
        self.runtime = self.base / (self.label + '.plist')
        self.login = self.home / 'Library/LaunchAgents' / (self.label + '.plist')

    def paths_ready(self):
        for p in (self.root, self.models):
            if not p.is_dir():
                raise ServiceError(f'目录不可用：{p}；请挂载原磁盘或选择已有目录，不会创建替代模型目录')
        # 即使误建了子目录，也拒绝未挂载的外置卷。
        for p in (self.root, self.models):
            if len(p.parts) > 2 and p.parts[1] == 'Volumes' and not os.path.ismount(Path('/Volumes') / p.parts[2]):
                raise ServiceError('外置卷未挂载：' + str(Path('/Volumes') / p.parts[2]))

    def command_config(self, model, server, port, backend="gguf"):
        if backend != "gguf":
            raise ServiceError('旧版 MLX 托管配置已停用；请手工打开 MLX-Serve 桌面 App 并开启 API。现有文件和进程未改动')
        model, server = checked_path(model), checked_path(server)
        if not model.is_file() or model.suffix.lower() != '.gguf':
            raise ServiceError('请选择已经下载的 .gguf 文件；不会下载或加载权重')
        if not model.resolve().is_relative_to((self.models / 'llm').resolve()):
            raise ServiceError('仅接受当前模型目录 llm/ 下的兼容 LLM；音频、ASR、图像 GGUF 不能交给 llama-server')
        if not server.is_file() or not os.access(server, os.X_OK):
            raise ServiceError('llama-server 不存在或不可执行')
        # llama-swap 会展开 ${...}，因此除命令转义外还禁止路径宏。
        if '${' in str(model) or '${' in str(server):
            raise ServiceError('路径含 llama-swap 宏语法 ${，不能用于托管配置')
        args = [str(server), '--model', str(model), '--alias', 'local-gguf', '--host',
                '127.0.0.1', '--port', str(port), '--ctx-size', '4096', '--parallel',
                '1', '--n-gpu-layers', '99', '--offline']
        return {'healthCheckTimeout': 600, 'globalTTL': 0, 'models': {
            'local-gguf': {'cmd': shlex.join(args), 'proxy': f'http://127.0.0.1:{port}', 'checkEndpoint': '/health'}}}

    def read_settings(self):
        data = json.loads(self.settings.read_text())
        keys = {'models_dir', 'model', 'server', 'port', 'backend_port', 'backend'}
        if not isinstance(data, dict) or set(data) not in (keys, keys | {'proxy_bind'}):
            raise ServiceError('托管设置结构无效，请重新配置')
        # 旧配置只读兼容，绝不在读取或启动时扩大网络访问范围。
        data['proxy_bind'] = proxy_bind(data.get('proxy_bind', '127.0.0.1'))
        if data['models_dir'] != str(self.models):
            raise ServiceError('当前模型目录已改变，请重新配置服务；不会使用上次目录')
        if data['backend'] == 'mlx':
            raise ServiceError('旧版 MLX 托管配置已停用；请手工打开 MLX-Serve 桌面 App 并开启 API。现有文件和进程未改动')
        if data['backend'] != 'gguf':
            raise ServiceError('未知推理后端')
        data['port'] = port_number(data['port'])
        data['backend_port'] = port_number(data['backend_port'])
        if data['port'] == data['backend_port']:
            raise ServiceError('llama-swap 和后端端口不能相同')
        return data

    def validate(self):
        self.paths_ready()
        data = self.read_settings()
        expected = self.command_config(data['model'], data['server'], data['backend_port'], data['backend'])
        actual = json.loads(self.config.read_text())
        if not matches_config(actual, expected, data['backend']):
            raise ServiceError('配置已改变或包含未托管命令；请重新配置。不会执行任意 YAML、启动钩子或预加载模型')
        return data

    def configure(self, model, server=None, port=8080, backend_port=18081, backend="gguf", bind="0.0.0.0"):
        if backend != 'gguf':
            raise ServiceError('MLX-Serve 由桌面 App 手工启动，不再配置命令行后端')
        bind = proxy_bind(bind)
        self.paths_ready()
        port, backend_port = port_number(port), port_number(backend_port)
        if port == backend_port:
            raise ServiceError('llama-swap 和后端端口不能相同')
        if self.loaded()['loaded']:
            raise ServiceError('请先停止本工具包服务，再修改配置')
        server = server or executable(self.root, 'llama-server')
        if not server:
            raise ServiceError('未找到 llama-server；请先安装 llama.cpp')
        config = self.command_config(model, server, backend_port, backend)
        data = dict(models_dir=str(self.models), model=str(checked_path(model)),
                    server=str(checked_path(server)), port=port, backend_port=backend_port, backend=backend, proxy_bind=bind)
        self.persist_runtime()
        atomic_write(self.config, json.dumps(config, ensure_ascii=False, indent=2).encode())
        atomic_write(self.settings, json.dumps(data, ensure_ascii=False, indent=2).encode())
        print('配置已保存，尚未启动服务、设置登录启动或加载权重。')
        print(access_notice(data))
        print('客户端模型名：' + next(iter(config['models'])))

    def set_bind(self, bind):
        bind = proxy_bind(bind)
        data = self.validate()
        if self.loaded()['loaded']:
            raise ServiceError('请先停止本工具包服务，再修改监听范围；不会停止 GUI 或其他实例')
        data['proxy_bind'] = bind
        self.persist_runtime()
        atomic_write(self.settings, json.dumps(data, ensure_ascii=False, indent=2).encode())
        print('llama-swap 访问范围已保存；模型、端口和后端配置保持不变，尚未启动。')
        print(access_notice(data))

    def import_config(self, path, port, bind="0.0.0.0"):
        # 仅接受已知安全配方，不接受任意命令配置。
        raw = checked_path(path).read_text()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                import yaml
            except ImportError:
                raise ServiceError('此 YAML 需要已安装的 PyYAML 才能只读解析；可改选 GGUF 生成配置，无需安装依赖')
            try:
                data = yaml.safe_load(raw)
            except yaml.YAMLError as exc:
                raise ServiceError('YAML 不是有效安全配置') from exc
        try:
            ids = set(data['models'])
            if ids != {'local-gguf'}:
                raise ValueError()
            backend = 'gguf'
            route = data['models']['local-gguf']
            tokens = shlex.split(route['cmd'])
            model = tokens[tokens.index('--model') + 1]
            server = tokens[2] if tokens[0] == '/usr/bin/env' else tokens[0]
            backend_port = port_number(tokens[tokens.index('--port') + 1])
            expected = self.command_config(model, server, backend_port, backend)
            if server != executable(self.root, 'llama-server'):
                raise ValueError()
            if not matches_config(data, expected, backend):
                raise ValueError()
        except (TypeError, KeyError, IndexError, ValueError):
            raise ServiceError('只支持本工具包的单 LLM 安全配置；任意命令、钩子或示例占位符不会导入。请重新生成')
        self.configure(model, server, port, backend_port, backend, bind)

    def catalog_path(self):
        here = Path(__file__).resolve().parent
        return here / 'catalog.json' if (here / 'catalog.json').is_file() else here.parent / 'catalog.json'

    def persist_runtime(self):
        # 用户确认配置时才保存稳定副本；状态检查绝不写入。
        here = Path(__file__).resolve().parent
        for name in ('service_manager.py', 'path_validation.py', 'terminal_ui.sh'):
            atomic_write(self.bundle / name, (here / name).read_bytes())
        atomic_write(self.bundle / 'catalog.json', self.catalog_path().read_bytes())

    def ctl(self, *args):
        return subprocess.run(['/bin/launchctl', *args], text=True, capture_output=True, timeout=10, check=False)

    def loaded(self):
        if sys.platform != 'darwin':
            return dict(loaded=False, owned=False, pid=None, detail='非 macOS，未查询 launchd')
        result = self.ctl('print', self.target)
        if result.returncode:
            return dict(loaded=False, owned=False, pid=None, detail='未加载')
        match = re.search(r'^\s*path = (.+)$', result.stdout, re.M)
        path = Path(match.group(1).strip()) if match else None
        owned = path in (self.runtime, self.login) and self.owned_plist(path)
        pid = re.search(r'^\s*pid = (\d+)$', result.stdout, re.M)
        return dict(loaded=True, owned=owned, pid=int(pid.group(1)) if pid else None,
                    detail='已加载' if owned else '同名服务归属无法确认；不会控制')

    def owned_plist(self, path):
        try:
            data = plistlib.loads(path.read_bytes())
            args = data.get('ProgramArguments', [])
            expected = self.program_args()
            return (data.get('Label') == self.label and data.get('MacAIKitRoot') == str(self.root)
                    and isinstance(args, list) and len(args) == 7
                    and args[:5] == expected[:5] and args[6:] == ['run']
                    and isinstance(args[5], str) and Path(args[5]).is_absolute())
        except (OSError, ValueError, plistlib.InvalidFileException):
            return False

    def program_args(self):
        return [sys.executable, str(self.bundle / 'service_manager.py'), '--root', str(self.root),
                '--models-dir', str(self.models), 'run']

    def plist(self):
        return plistlib.dumps(dict(Label=self.label, ProgramArguments=self.program_args(),
            MacAIKitRoot=str(self.root), RunAtLoad=True, KeepAlive=False,
            StandardOutPath=str(self.base / 'stdout.log'), StandardErrorPath=str(self.base / 'stderr.log'),
            EnvironmentVariables={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}))

    def require_mac(self):
        if sys.platform != 'darwin':
            raise ServiceError('启停与登录启动仅支持 macOS 用户级 launchd；未更改系统')

    def start(self):
        self.require_mac()
        data = self.validate()
        print(access_notice(data))
        if not executable(self.root, 'llama-swap'):
            raise ServiceError('llama-swap 未安装，请先安装能力 → llama-swap')
        state = self.loaded()
        if state['loaded']:
            if not state['owned']:
                raise ServiceError(state['detail'])
            if state['pid']:
                print('本工具包服务已运行，无需重复启动。')
                return
            result = self.ctl('bootout', self.target)
            if result.returncode:
                raise ServiceError('旧服务未能卸载；未启动新实例')
        available_port(data['port'], data['proxy_bind'])
        available_port(data['backend_port'])
        if self.runtime.exists() and not self.owned_plist(self.runtime):
            raise ServiceError('现有启动文件归属不符，未覆盖')
        atomic_write(self.runtime, self.plist())
        enabled = self.ctl('enable', self.target)
        if enabled.returncode:
            raise ServiceError('launchd 未能启用服务')
        result = self.ctl('bootstrap', f'gui/{os.getuid()}', str(self.runtime))
        if result.returncode:
            raise ServiceError('launchd 启动失败；请查看服务状态和日志：' + str(self.base))
        for _ in range(30):
            if self.loaded()['pid'] and self.health(data['port']):
                print(f'llama-swap 已启动：http://127.0.0.1:{data["port"]}；尚未加载模型，推理请求才会加载。')
                return
            time.sleep(0.3)
        raise ServiceError('已提交启动，但未确认健康；请查看服务状态及 stderr.log。不会自动重复启动')

    def stop(self):
        self.require_mac()
        state = self.loaded()
        if not state['loaded']:
            print('本工具包服务未加载；没有停止其他进程。')
            return
        if not state['owned']:
            raise ServiceError(state['detail'])
        if self.ctl('bootout', self.target).returncode:
            raise ServiceError('停止失败，请检查服务状态')
        print('已停止本工具包 llama-swap；登录启动设置保持不变。')

    def enable_login(self):
        self.require_mac()
        data = self.validate()
        print(access_notice(data))
        if not executable(self.root, 'llama-swap'):
            raise ServiceError('请先安装 llama-swap')
        if self.login.exists() and not self.owned_plist(self.login):
            raise ServiceError('登录启动文件归属不符，未覆盖')
        atomic_write(self.login, self.plist())
        if self.ctl('enable', self.target).returncode:
            raise ServiceError('登录启动文件已保存，但 launchd 启用未确认；请查看状态')
        print(f'已设置下次用户登录启动，监听 {data["proxy_bind"]}:{data["port"]}；没有立即启动。可选择“取消登录启动”撤销。')

    def disable_login(self):
        self.require_mac()
        if self.login.exists():
            if not self.owned_plist(self.login):
                raise ServiceError('登录启动文件归属不符，未删除')
            self.login.unlink()
        print('已取消下次登录启动；当前运行状态不变。如需停止，请另选停止服务。')

    def health(self, port):
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
        try:
            conn.request('GET', '/health')
            response = conn.getresponse()
            return response.status == 200 and response.read(32).strip() == b'OK'
        except (OSError, http.client.HTTPException):
            return False
        finally:
            conn.close()

    def status(self):
        print('llama-swap 服务状态（只读；不请求推理，不加载权重）')
        print('可执行入口：' + (executable(self.root, 'llama-swap') or '未安装'))
        print('配置路径：' + str(self.config))
        data = None
        try:
            data = self.validate()
            print('配置：存在，已通过托管结构与本地路径检查；模型兼容性尚未运行验证')
        except (OSError, ValueError) as exc:
            print('配置：不可用 — ' + str(exc))
        state = self.loaded()
        print('运行：' + state['detail'] + ('；PID ' + str(state['pid']) if state['pid'] else '；无已确认 PID'))
        print('登录启动：' + ('已配置' if self.owned_plist(self.login) else '未配置或归属不符'))
        if data and sys.platform == 'darwin':
            disabled = self.ctl('print-disabled', f'gui/{os.getuid()}')
            match = re.search(r'"' + re.escape(self.label) + r'"\s*=>\s*(true|false)', disabled.stdout)
            print('launchd 启用状态：' + ('未核实' if disabled.returncode else '禁用' if match and match[1] == 'true' else '启用（默认或显式）'))
        if data:
            route_id = next(iter(json.loads(self.config.read_text())['models']))
            print('客户端模型名：' + route_id)
            print(f'配置监听：{data["proxy_bind"]}:{data["port"]}；后端：127.0.0.1:{data["backend_port"]}（按需）')
            print(f'本机 API：http://127.0.0.1:{data["port"]}/v1')
            if data['proxy_bind'] == '0.0.0.0':
                print(f'另一台机器 API：http://<这台 Mac 的局域网 IP>:{data["port"]}/v1（请替换 IP；不能填 0.0.0.0 或另一台机器自己的 localhost）')
            else:
                print('当前为仅本机配置；旧版设置保持回环。要启用局域网，请先停止 llama-swap，再选“修改 llama-swap 访问范围”，无需重选模型。')
            print(access_notice(data))
            healthy = state['owned'] and state['pid'] and self.health(data['port'])
            print('llama-swap 健康：' + ('/health 返回 OK（不代表模型已加载）' if healthy else '未确认；未请求模型接口'))
        print('日志：' + str(self.base / 'stderr.log'))
        print('操作：可选 llama-swap → 配置兼容 GGUF → 启动 llama-swap；登录启动是独立选项。')
        print('MLX-Serve：请手工打开桌面 App、加载模型并开启 API；不由 llama-swap 托管。')

    def run(self):
        self.require_mac()
        data = self.validate()  # 登录时也执行检查；卷未挂载则拒绝启动。
        binary = executable(self.root, 'llama-swap')
        if not binary:
            raise ServiceError('找不到 llama-swap')
        available_port(data['port'], data['proxy_bind'])
        available_port(data['backend_port'])
        os.execv(binary, [binary, '-config', str(self.config), '-listen', f'{data["proxy_bind"]}:{data["port"]}'])


def confirm(message):
    print(message)
    return sys.stdin.isatty() and input('输入 yes 确认，其他内容取消：').strip().lower() == 'yes'


def menu_working_directory():
    """菜单子进程不继承可能已被更新/删除的调用目录。"""
    directory = Path(__file__).resolve().parents[1]
    if not (directory / 'scripts/terminal_ui.sh').is_file():
        raise ServiceError('工具包目录已被移动或替换，请从新目录重新运行 bash start.sh。')
    return str(directory)


def select_menu():
    # 复用主菜单方向键和 Esc 语义；选择值经独立管道返回，不拼接用户输入。
    read_fd, write_fd = os.pipe()
    script = Path(__file__).resolve().parent / 'terminal_ui.sh'
    command = '. "$1"; UI_BACK_LABEL=返回; ui_select "服务管理 · llama-swap" '
    command += '"查看状态" "安装、配置、PID、健康与登录启动" '
    command += '"配置兼容 LLM GGUF" "不是 ASR、音乐或图像 GGUF" '
    command += '"选择已有安全配置" "仅导入已知安全配方，不执行任意 YAML" '
    command += '"启动 llama-swap" "按配置监听；局域网模式会暴露推理入口" '
    command += '"停止 llama-swap" "仅停止本工具包服务，可能中断请求" '
    command += '"设置登录启动" "下次用户登录生效，不立即启动" '
    command += '"取消登录启动" "保留当前运行实例，可另行停止" '
    command += '"修改 llama-swap 访问范围" "保留模型与端口；旧配置可显式改为局域网"; printf "%s" "$UI_CHOICE" >&"$2"'
    try:
        subprocess.run(['/bin/bash', '-c', command, 'service-menu', str(script), str(write_fd)],
                       pass_fds=(write_fd,), check=True, cwd=menu_working_directory())
        os.close(write_fd)
        write_fd = -1
        return os.read(read_fd, 32).decode().strip()
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def pause_menu():
    # 复用 Bash 单键读取；不影响路径和端口等文本输入。
    script = Path(__file__).resolve().parent / 'terminal_ui.sh'
    subprocess.run(['/bin/bash', '-c', '. "$1"; ui_pause', 'menu-pause', str(script)], check=True, cwd=menu_working_directory())


def menu(manager):
    while True:
        choice = select_menu()
        try:
            if choice in ('0', 'q', ''):
                return
            if choice in ('1', 'h'):
                manager.status()
            elif choice == '2':
                model = input('当前模型目录 llm/ 下已核验兼容的 LLM GGUF 绝对路径（不接受 ASR、音乐、图像）：').strip()
                port = input('llama-swap 端口 [8080]：').strip() or '8080'
                backend = input('按需 llama-server 端口 [18081]：').strip() or '18081'
                bind = choose_proxy_bind()
                if confirm('确认该文件是 llama.cpp 兼容的 LLM（本检查不证明架构兼容）。保存单 GGUF 配置；使用已安装的 llama-server，后端仅回环访问。不启动、不下载。已有托管配置将被替换。'):
                    manager.configure(model, port=port, backend_port=backend, bind=bind)
            elif choice == '3':
                path = input('已有配置的绝对路径（仅接受本工具包安全格式）：').strip()
                port = input('llama-swap 端口 [8080]：').strip() or '8080'
                bind = choose_proxy_bind()
                if confirm('验证并复制安全配置，替换已有托管配置；任意命令和启动钩子不会导入。'):
                    manager.import_config(path, port, bind)
            elif choice == '4' and confirm(access_notice(manager.validate()) + '启动 llama-swap，不预加载权重；请先停止 GUI 中重复模型，避免端口冲突。'):
                manager.start()
            elif choice == '5' and confirm('停止本工具包 llama-swap 及其管理的后端，正在进行的请求可能中断；不停止其他服务。'):
                manager.stop()
            elif choice == '6' and confirm(access_notice(manager.validate()) + '写入当前用户 LaunchAgent：下次登录启动 llama-swap，不立即启动、不预加载权重。外置卷不可用时拒绝启动。可用选项 7 撤销。'):
                manager.enable_login()
            elif choice == '7' and confirm('取消本工具包下次登录启动，保留当前服务；要立即停止请另选 5。'):
                manager.disable_login()
            elif choice == '8':
                bind = choose_proxy_bind()
                if confirm('仅保存 llama-swap 访问范围并更新托管运行脚本，保留模型和端口。需先停止 llama-swap；不启动、不改变防火墙。'):
                    manager.set_bind(bind)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print('未完成：' + str(exc))
        pause_menu()


def main(argv=None):
    parser = argparse.ArgumentParser(description='独立服务管理：默认仅查看；显式启停 llama-swap，不安装或加载权重')
    parser.add_argument('--root', default=str(Path.home() / 'AI'))
    parser.add_argument('--models-dir', default=str(Path.home() / 'AI/models'))
    parser.add_argument('action', nargs='?', choices=['menu', 'status', 'configure', 'start', 'stop', 'enable-login', 'disable-login', 'set-bind', 'run'])
    parser.add_argument('--model', help='已有兼容 LLM GGUF 文件')
    parser.add_argument('--backend', choices=['gguf'], default='gguf')
    parser.add_argument('--server', help='已安装 llama-server 的绝对路径')
    parser.add_argument('--config', help='导入本工具包安全格式 JSON/YAML，拒绝任意命令')
    parser.add_argument('--proxy-bind', choices=['0.0.0.0', '127.0.0.1'], default=None, help='新配置默认 0.0.0.0；旧配置需显式 set-bind 才会改变')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--backend-port', type=int, default=None)
    parser.add_argument('--yes', action='store_true', help='仅确认本次显式变更，不用于安装或下载')
    args = parser.parse_args(argv)
    try:
        manager = Manager(args.root, args.models_dir)
        action = args.action or ('menu' if sys.stdin.isatty() else 'status')
        if args.proxy_bind is not None and action not in ('configure', 'set-bind'):
            raise ServiceError('--proxy-bind 仅用于 configure 或 set-bind；启动沿用已保存的监听范围')
        if action == 'menu':
            if not sys.stdin.isatty():
                manager.status()
            else:
                menu(manager)
        elif action == 'status':
            manager.status()
        elif action == 'run':
            manager.run()
        else:
            impacts = {'configure': '保存或替换托管配置，不启动。', 'start': '按保存的监听范围启动 llama-swap，不预加载模型。',
                       'stop': '停止本工具包 llama-swap，正在进行的请求可能中断。',
                       'enable-login': '设置下次用户登录启动 llama-swap，不立即启动；disable-login 可撤销。',
                       'disable-login': '取消下次登录启动，保留当前运行实例。',
                       'set-bind': '仅修改 llama-swap 访问范围并更新运行脚本，保留模型与端口。'}
            if action in ('configure', 'set-bind'):
                if action == 'set-bind' and args.proxy_bind is None:
                    raise ServiceError('set-bind 必须显式提供 --proxy-bind')
                print(access_notice({'proxy_bind': args.proxy_bind or '0.0.0.0'}))
            elif action in ('start', 'enable-login'):
                print(access_notice(manager.validate()))
            if not args.yes and not confirm(impacts[action]):
                print('已取消；非交互操作需显式 --yes。')
                return 2
            if action == 'configure':
                if args.config:
                    manager.import_config(args.config, args.port, args.proxy_bind or '0.0.0.0')
                elif args.model:
                    manager.configure(args.model, args.server, args.port, args.backend_port or 18081, args.backend, args.proxy_bind or '0.0.0.0')
                else:
                    raise ServiceError('配置需要 --model 已有LLM路径 或 --config 安全配置路径')
            elif action == 'set-bind':
                manager.set_bind(args.proxy_bind)
            else:
                getattr(manager, action.replace('-', '_'))()
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print('服务操作未完成：' + str(exc), file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print('\n已取消。')
        return 130


if __name__ == '__main__':
    sys.exit(main())
