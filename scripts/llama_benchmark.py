#!/usr/bin/env python3
"""测试所选无认证接口，不管理服务生命周期。"""

from __future__ import annotations

import argparse
import codecs
import csv
from datetime import datetime, timezone
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

PACKAGE = 'llama-benchy==0.4.0'
DEFAULT_URL = 'http://127.0.0.1:11234/v1'
ROOT = Path(__file__).resolve().parents[1]
# 按文件位置导入自有模块，支持独立入口与离线 import 测试。
sys.path.insert(0, str(ROOT / 'scripts'))
from path_validation import validate_model_root
from benchmark_discovery import discover, choose, resolve_tokenizer, default_model_root


def default_url(root, models_dir):
    # 两种 Benchmark 均连接已有后端，不继承 llama-swap 代理端口。
    return DEFAULT_URL


def endpoint(value: str) -> str:
    if any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError('接口地址不能含空白或控制字符')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError('接口地址或端口无效') from None
    if (
        parsed.scheme not in ('http', 'https')
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or '?' in value
        or '#' in value
        or '\\' in value
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError('仅支持无账号密码、查询参数或片段的 HTTP(S) 接口地址')
    return value.rstrip('/') + ('/v1' if parsed.path in ('', '/') else '')


def identifier(value: str) -> str:
    if not value or value.startswith('-') or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError('模型名称必填，不能含空白、控制字符或以横线开头')
    return value


def tokenizer_identifier(value: str) -> str:
    if value.startswith(('~/', '/')):
        path = Path(value).expanduser()
        if any(ord(c) < 32 or ord(c) == 127 for c in value) or not path.is_dir():
            raise ValueError('本地 tokenizer 须为存在的目录')
        return str(path.resolve())
    return identifier(value)


def depths(value: str) -> list[int]:
    if not re.fullmatch(r'[0-9]+', value):
        raise ValueError('最大背景深度须为整数')
    maximum = int(value)
    if not 4 <= maximum <= 2_000_000:
        raise ValueError('最大背景深度范围：4–2000000')
    return [0, maximum // 4, maximum // 2, maximum]


def default_report_root() -> Path:
    # 固定在工具包内，不受调用时所在目录影响。
    return ROOT / 'output' / 'reports'


def output_directory(value: str) -> Path:
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('输出目录不能含控制字符')
    path = Path(value).expanduser()
    if not path.is_absolute() or '..' in path.parts or path == Path('/'):
        raise ValueError('请使用绝对目录（可用 ~/），不能是 / 或含 ..')
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError('请选择真实目录，不使用符号链接目录')
    if path.parts[:2] == ('/', 'Volumes'):
        if len(path.parts) < 3 or not os.path.ismount(Path('/Volumes') / path.parts[2]):
            raise ValueError('外置磁盘尚未挂载')
    return path


def command(uvx: str, url: str, model: str, tokenizer: str, points: list[int], result: Path) -> list[str]:
    return [
        uvx,
        '--no-config',
        '--from',
        PACKAGE,
        'python',
        '-u',
        str(ROOT / 'scripts/benchmark_runner.py'),
        '--base-url',
        url,
        '--model',
        model,
        '--tokenizer',
        tokenizer,
        '--depth',
        *map(str, points),
        '--pp',
        '2048',
        '--tg',
        '480',
        '--latency-mode',
        'generation',
        '--skip-coherence',
        '--save-result',
        str(result),
        '--runs',
        '1',
        '--format',
        'csv',
        '--exit-on-first-fail',
        '--concurrency',
        '1',
    ]


def clean_environment(home: Path, cache: Path | None = None) -> dict[str, str]:
    # 不继承密钥、令牌、代理凭据、Python 注入变量或用户配置。
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'SYSTEMROOT') if key in os.environ}
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / 'config'),
        XDG_CACHE_HOME=str(home / 'cache'),
        HF_HOME=str(home / 'huggingface'),
        HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
        UV_NO_CONFIG='1',
        UV_KEYRING_PROVIDER='disabled',
        NO_COLOR='1',
        TERM='dumb',
        PYTHONUNBUFFERED='1',
    )
    if cache is not None:
        env.update(UV_CACHE_DIR=str(cache / 'uv'), HF_HUB_CACHE=str(cache / 'hub'))
    return env


def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    # 仅解析保存的结构化文件，不从终端日志或 ANSI 输出中提取表格。
    if path.stat().st_size > 8_000_000:
        raise ValueError('CSV 文件过大，未在终端展开')
    text = path.read_text(encoding='utf-8-sig')
    if any(ord(c) < 32 and c not in '\r\n\t' for c in text) or '\x7f' in text:
        raise ValueError('CSV 含控制字符')
    rows = list(csv.reader(io.StringIO(text, newline=''), strict=True))
    if len(rows) < 2 or not rows[0] or len(set(rows[0])) != len(rows[0]):
        raise ValueError('CSV 缺少有效表头或结果')
    if any(not h.strip() for h in rows[0]) or any(len(r) != len(rows[0]) for r in rows[1:]):
        raise ValueError('CSV 列数不一致')
    return rows[0], rows[1:]


def summary(headers: list[str], rows: list[list[str]], points: list[int], model: str) -> list[tuple]:
    required = {'model', 'test_name', 't_s_mean', 'e2e_ttft_mean'}
    if not required.issubset(headers):
        raise ValueError('CSV 表头不符合 llama-benchy 0.4.0')
    records = [dict(zip(headers, row)) for row in rows]
    if any(record['model'] != model for record in records):
        raise ValueError('CSV 模型名称与本次请求不一致')
    expected = {
        f'{kind}{size}' + (f' @ d{d}' if d else '') for d in points for kind, size in [('pp', 2048), ('tg', 480)]
    }
    if len(records) != len(expected) or {r['test_name'] for r in records} != expected:
        raise ValueError('测试未完成：缺少预期的四档 pp/tg 结果，或出现额外结果')
    by_name = {r['test_name']: r for r in records}

    def metric(value: str, mandatory: bool = True) -> str:
        if not value and not mandatory:
            return '—'
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError('结果包含无效或非正数指标')
        return f'{number:.2f}' if number < 1_000_000 else f'{number:.2e}'

    output = []
    for d in points:
        suffix = f' @ d{d}' if d else ''
        pp, tg = by_name['pp2048' + suffix], by_name['tg480' + suffix]
        output.append((d, metric(pp['t_s_mean']), metric(tg['t_s_mean']), metric(pp['e2e_ttft_mean'], False)))
    return output


def table(records: list[tuple], width: int | None = None) -> str:
    width = width or shutil.get_terminal_size((80, 24)).columns
    narrow = width < 55
    heading = '   背景    PP tok/s   TG tok/s'
    if not narrow:
        heading += '   端到端首 token ms'
    lines = [heading, '─' * (29 if narrow else 50)]
    for depth, pp, tg, ttft in records:
        line = f'{depth:7d} {pp:>10} {tg:>10}'
        if not narrow:
            line += f' {ttft:>18}'
        lines.append(line)
    return '\n'.join(lines)


def unreliable_logs(folder: Path) -> bool:
    markers = (b"Falling back to 'gpt2' tokenizer as approximation", b'assuming 1 token per chunk')
    for name in ('stdout.log', 'stderr.log'):
        with (folder / name).open('rb') as log:
            for line in log:
                if any(marker in line for marker in markers):
                    return True
    return False


def ask(label: str, default: str, validator):
    while True:
        value = input(f'{label} [{default}]（q 取消）：').strip()
        if value.lower() == 'q':
            raise EOFError
        try:
            return validator(value or default)
        except ValueError as exc:
            print(str(exc))


class LiveLog:
    """旁路读取原始日志，不用 PIPE 阻塞子进程；终端过滤可跨块的控制序列。"""

    def __init__(self, path, label):
        self.file = path.open('rb')
        self.label = label
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.state = 'text'

    def safe_text(self, text):
        output = []
        for char in text:
            if self.state == 'escape':
                self.state = 'csi' if char == '[' else 'string' if char in ']PX^_' else 'text'
            elif self.state == 'csi':
                if '@' <= char <= '~':
                    self.state = 'text'
            elif self.state == 'string':
                if char in ('\x07', '\x9c'):
                    self.state = 'text'
                elif char == '\x1b':
                    self.state = 'string_escape'
            elif self.state == 'string_escape':
                self.state = 'text' if char == '\\' else 'string'
            elif char == '\x1b':
                self.state = 'escape'
            elif char == '\x9b':
                self.state = 'csi'
            elif char in ('\x90', '\x9d', '\x9e', '\x9f'):
                self.state = 'string'
            elif char == '\r':
                output.append('\n')
            elif char in ('\n', '\t') or char.isprintable():
                output.append(char)
        return ''.join(output)

    def drain(self):
        raw = self.file.read(16384)
        if raw:
            text = self.safe_text(self.decoder.decode(raw))
            if text:
                print(f'[{self.label}] ' + text, end='' if text.endswith('\n') else '\n', flush=True)
        return bool(raw)

    def close(self):
        self.file.close()


def run(
    uvx: str, url: str, model: str, tokenizer: str, points: list[int], output: Path, resolution: dict | None = None
) -> int:
    output_directory(str(output))
    output.mkdir(parents=True, exist_ok=True)
    cache = output_directory(str(output / '.llama-benchy-cache'))
    cache.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    folder = Path(tempfile.mkdtemp(prefix=f'llama-benchmark-{stamp}-', dir=output))
    result = folder / f'llama_{stamp}.csv'
    runtime = folder / 'runtime-home'
    runtime.mkdir(mode=0o700)
    original_tokenizer = tokenizer
    if Path(tokenizer).is_absolute():
        source = Path(tokenizer) / 'tokenizer.json'
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 100_000_000:
            raise ValueError('本地 tokenizer.json 缺失、是符号链接或超过100MB')
        snapshot = folder / 'tokenizer'
        snapshot.mkdir(mode=0o700)
        with source.open('rb') as src, (snapshot / 'tokenizer.json').open('xb') as dst:
            shutil.copyfileobj(src, dst)
        (snapshot / 'tokenizer.json').chmod(0o600)
        tokenizer = str(snapshot)
    metadata = {
        'tool': PACKAGE,
        'created_utc': stamp,
        'base_url': url,
        'model': model,
        'tokenizer': tokenizer,
        'tokenizer_original': original_tokenizer,
        'tokenizer_resolution': resolution,
        'depths': points,
        'pp': 2048,
        'tg': 480,
        'runs': 1,
        'latency_mode': 'generation',
        'skip_coherence': True,
        'csv': result.name,
    }
    with (folder / 'run.json').open('x', encoding='utf-8') as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
        handle.write('\n')

    def html_report(failed=False, warning=''):
        # 报告失败不掩盖原测试退出状态，原 CSV 与日志始终保留。
        from benchy_report import save_report

        try:
            headers, rows = read_csv(result)
            report = save_report(folder, headers, rows, metadata, failed=failed, warning=warning)
            print(f'HTML：{report}')
        except (OSError, ValueError, csv.Error, UnicodeError) as exc:
            print(f'HTML 未生成：{exc}')

    print(f'报告目录：{folder}')
    print('准备测试依赖并启动执行器；下方实时显示 stdout / stderr（Ctrl-C 中断）。', flush=True)
    print(f'原始日志：{folder / "stdout.log"} 和 {folder / "stderr.log"}', flush=True)
    process = None
    tails = []
    try:
        with (folder / 'stdout.log').open('xb') as stdout, (folder / 'stderr.log').open('xb') as stderr:
            process = subprocess.Popen(
                command(uvx, url, model, tokenizer, points, result),
                cwd=folder,
                env=clean_environment(runtime, cache),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            tails = [LiveLog(folder / 'stdout.log', 'stdout'), LiveLog(folder / 'stderr.log', 'stderr')]
            started = last = time.monotonic()
            while process.poll() is None:
                for tail in tails:
                    tail.drain()
                time.sleep(0.2)
                now = time.monotonic()
                if now - last >= 10:
                    print(f'已运行 {int(now - started)} 秒 · 等待测试进展（Ctrl-C 中断）', flush=True)
                    last = now
            for tail in tails:
                while tail.drain():
                    pass
        code = process.returncode
    except KeyboardInterrupt:
        import signal

        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        html_report(failed=True, warning='用户中断；部分数据不纳入摘要。')
        print('\n已中断；已有日志保留。')
        return 130
    finally:
        for tail in tails:
            tail.close()
    if code:
        html_report(failed=True)
        print(f'测试未完成（退出码 {code}）。已有 CSV / 日志保留：{folder}')
        return code if 0 < code <= 125 else 1
    try:
        headers, rows = read_csv(result)
        records = summary(headers, rows, points, model)
        if unreliable_logs(folder):
            raise ValueError('分词器或 token 计数已回退为近似值，测速不准确')
    except (OSError, ValueError, csv.Error, UnicodeError) as exc:
        html_report(failed=True, warning=str(exc))
        print(f'未获得有效 CSV：{exc}；已有 CSV / 日志保留：{folder}')
        return 1
    html_report()
    print('PP：输入处理速度；TG：生成速度')
    print(table(records))
    print(f'CSV：{result}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description='自动读取 /models 并匹配 tokenizer，测试所选接口并保存报告。', add_help=False
    )
    parser.add_argument('-h', '--help', action='help', help='显示帮助并退出')
    parser.add_argument('--root', default=str(Path.home() / 'AI'), help='托管服务所在工具目录，与服务管理 --root 一致')
    parser.add_argument('--base-url', help='显式指定接口，覆盖默认 11234 地址')
    parser.add_argument('--models-dir', type=str, help='模型根目录；默认沿用菜单保存的设置')
    parser.add_argument('--output', help='报告父目录；默认项目内 output/reports，每次新建时间戳子目录')
    args = parser.parse_args()
    try:
        args.models_dir = validate_model_root(args.models_dir if args.models_dir is not None else default_model_root())
        args.root = validate_model_root(args.root)
    except (ValueError, OSError) as exc:
        print(f'无法完成：{exc}', file=sys.stderr)
        return 1
    uvx = shutil.which('uvx')
    if not uvx:
        print('需要已安装的 uvx；请先在「安装运行工具」准备基础环境。不会自动安装。')
        return 1
    print('Llama benchmark')
    try:
        url = (
            endpoint(args.base_url)
            if args.base_url is not None
            else ask('接口地址', default_url(args.root, args.models_dir), endpoint)
        )
        rows = discover(url)
        selected = choose(rows, '请选择测试模型：', lambda row: row['id'])
        model = selected['id']
        resolution = resolve_tokenizer(selected, args.models_dir)
        tokenizer = resolution['value']
        points = ask('最大背景深度', '200000', depths)
        output = (
            output_directory(args.output)
            if args.output is not None
            else ask('报告目录', str(default_report_root()), output_directory)
        )
        print(f'\n接口：{url}\n模型：{model}')
        print(f'背景深度：{", ".join(map(str, points))}\npp 2048 · tg 480 · runs 1')
        print(f'名义总上下文至少 {points[-1] + 2528} token，另留模板余量。')
        print(f'报告目录：{output}')
        if resolution['kind'] == 'public':
            print(
                f'本地未找到同源 tokenizer；将联网从 Hugging Face {tokenizer} 仅下载 tokenizer.json，不下载模型权重、不执行模型代码。'
            )
        return run(uvx, url, model, tokenizer, points, output, resolution)
    except (EOFError, KeyboardInterrupt):
        print('\n已取消。')
        return 0
    except (OSError, ValueError) as exc:
        print(f'无法完成：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
