"""用真实伪终端验证菜单按键、取消和终端状态，无安装、下载或远程操作。"""

import os
import errno
import fcntl
import re
import struct
import unicodedata
from pathlib import Path
import pty
import select
import signal
import sys
import subprocess
import tempfile
import termios
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Terminal:
    def __init__(self, columns=80, rows=24, bash_options=(), **env):
        self.master, self.slave = pty.openpty()
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        self.before = termios.tcgetattr(self.slave)
        self.home = tempfile.TemporaryDirectory()
        environment = dict(
            os.environ,
            TERM='xterm-256color',
            HOME=self.home.name,
            XDG_CONFIG_HOME=str(Path(self.home.name) / '.config'),
        )
        environment.pop('NO_COLOR', None)
        environment.update(env)
        self.process = subprocess.Popen(
            ['bash', *bash_options, str(ROOT / 'start.sh')],
            stdin=self.slave,
            stdout=self.slave,
            stderr=self.slave,
            env=environment,
            start_new_session=True,
        )
        self.output = b''

    def until(self, text, start=0):
        target = text.encode()
        deadline = time.monotonic() + 5
        while target not in self.output[start:] and time.monotonic() < deadline:
            if select.select([self.master], [], [], 0.1)[0]:
                try:
                    self.output += os.read(self.master, 65536)
                except OSError:
                    break
        if target not in self.output[start:]:
            raise AssertionError(f'Missing {text!r}: {self.output.decode(errors="replace")}')

    def menu(self, text, back, start=0):
        # 标题可能先于整帧到达；先读到该帧页脚，再发送按键或等待退出，
        # 避免伪终端输出尚未读完造成阻塞。
        self.until(text, start)
        frame_start = self.output.index(text.encode(), start)
        self.until('0/q ' + back, frame_start)

    def send(self, value):
        start = len(self.output)
        os.write(self.master, value)
        return start

    def wait(self, timeout=5):
        # macOS 伪终端输出缓冲较小；等待退出时持续读取，避免子进程写满后阻塞。
        deadline = time.monotonic() + timeout
        while self.process.poll() is None and time.monotonic() < deadline:
            if select.select([self.master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(self.master, 65536)
                    if chunk:
                        self.output += chunk
                except OSError:
                    pass
        if self.process.poll() is None:
            raise subprocess.TimeoutExpired(self.process.args, timeout)
        return self.process.returncode

    def assert_restored(self, case):
        try:
            after = termios.tcgetattr(self.slave)
        except termios.error as exc:
            # macOS 在伪终端最后一个子进程退出后可能拒绝再次读取属性。
            if sys.platform == 'darwin' and exc.args[0] == errno.ENOTTY and self.process.poll() is not None:
                return
            raise
        case.assertEqual(self.before, after)

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        os.close(self.master)
        os.close(self.slave)
        self.home.cleanup()


class NavigationTests(unittest.TestCase):
    def terminal(self, **env):
        t = Terminal(**env)
        self.addCleanup(t.close)
        return t

    def test_pause_left_and_escape_without_enter(self):
        for key in (b'\x1b[D', b'\x1bOD', b'\x1b'):
            with self.subTest(key=key):
                t = self.terminal()
                t.menu('从这里开始', '退出')
                mark = t.send(b'h\r')
                t.until('Enter / Esc / ← 返回菜单', mark)
                mark = t.send(key)
                t.menu('从这里开始', '退出', mark)
                # 再进一个菜单，确认没有残留的转义字节误操作。
                mark = t.send(b'2\r')
                t.menu('模型管理', '返回', mark)
                mark = t.send(b'\x1b[D')
                t.menu('从这里开始', '退出', mark)
                t.send(b'0\r')
                self.assertEqual(t.wait(timeout=5), 0)
                t.assert_restored(self)

    def test_service_log_and_swap_back_without_extra_pause(self):
        t = self.terminal(columns=40, rows=24)
        t.menu('从这里开始', '退出')
        mark = t.send(b'6\r')
        t.menu('服务管理', '返回', mark)
        for key in (b'\x1b[D', b'\x1b'):
            mark = t.send(b'6\r')
            t.menu('查看日志', '返回', mark)
            mark = t.send(key)
            t.menu('启动全部', '返回', mark)
            self.assertNotIn('返回菜单…'.encode(), t.output[mark:])
        mark = t.send(b'7\r')
        t.menu('服务管理 · llama-swap', '返回', mark)
        mark = t.send(b'\x1b[D')
        t.menu('启动全部', '返回', mark)
        mark = t.send(b'\x1b[D')
        t.menu('从这里开始', '退出', mark)
        self.assertNotIn('返回菜单…'.encode(), t.output[mark:])
        t.send(b'0\r')
        self.assertEqual(t.wait(timeout=5), 0)

    def test_service_single_start_choices_fit_and_cancel_readonly(self):
        for columns in (40, 80):
            with self.subTest(columns=columns):
                t = self.terminal(columns=columns, rows=24)
                t.menu('从这里开始', '退出')
                mark = t.send(b'6\r')
                t.menu('服务管理', '返回', mark)
                frame = self.assert_screen_fits(t.output[mark:].decode(), columns, 24)
                for label in (
                    '启动全部',
                    '仅启动音频',
                    '仅启动制图',
                    '停止全部',
                    '查看状态',
                    '查看日志',
                    '可选 llama-swap',
                    '切换访问范围',
                ):
                    self.assertIn(label, frame)
                mark = t.send(b'\x1b[D')
                t.menu('从这里开始', '退出', mark)
                t.send(b'0\r')
                self.assertEqual(t.wait(timeout=5), 0)
                self.assertFalse((Path(t.home.name) / 'AI').exists())

    def test_plain_terminal_left_is_immediate(self):
        t = self.terminal(NO_COLOR='1')
        t.until('请选择编号：')
        mark = t.send(b'2\r')
        t.until('模型管理', mark)
        mark = t.send(b'\x1b[D')
        t.until('从这里开始', mark)
        t.send(b'0\r')
        self.assertEqual(t.wait(timeout=5), 0)

    def test_arrow_enter_left_and_numeric_exit_restore_terminal(self):
        t = self.terminal()
        t.until('0/q 退出')
        mark = t.send(b'\x1b[B\r')
        t.menu('模型管理', '返回', mark)
        mark = t.send(b'\x1b[D')
        t.menu('从这里开始', '退出', mark)
        t.send(b'0\r')
        self.assertEqual(t.wait(timeout=5), 0)
        t.assert_restored(self)
        self.assertIn(b'\x1b[2J\x1b[H', t.output)
        self.assertFalse((Path(t.home.name) / 'AI').exists())

    def test_up_wraps_and_right_enters_benchmark(self):
        t = self.terminal()
        t.until('0/q 退出')
        mark = t.send(b'\x1b[A\x1b[A\x1b[C')
        t.menu('Llama benchmark', '返回', mark)
        frame = self.assert_screen_fits(t.output[mark:].decode(), 80, 24)
        self.assertIn('Benchmark', frame)
        self.assertIn('MLX-Serve 长上下文', frame)
        mark = t.send(b'0\r')
        t.menu('从这里开始', '退出', mark)
        mark = t.send(b'4\r')
        t.menu('查看与更新 API 说明', '返回', mark)
        mark = t.send(b'0\r')
        t.menu('从这里开始', '退出', mark)
        t.send(b'q\r')
        self.assertEqual(t.wait(timeout=5), 0)

    def test_install_submenu_cancel_has_no_side_effect(self):
        t = self.terminal()
        t.until('0/q 退出')
        mark = t.send(b'1\r')
        t.menu('安装推理与生成引擎', '返回', mark)
        self.assertNotIn('仅安装 MLX 命令行'.encode(), t.output[mark:])
        time.sleep(0.1)
        self.assertNotIn('基础环境安装'.encode(), t.output[mark:])
        mark = t.send(b'0\r')
        t.menu('从这里开始', '退出', mark)
        t.send(b'0\r')
        self.assertEqual(t.wait(timeout=5), 0)
        self.assertEqual(list(Path(t.home.name).iterdir()), [])

    def test_modified_arrow_does_not_leak_numeric_selection(self):
        t = self.terminal()
        t.until('0/q 退出')
        mark = t.send(b'\x1b[1;5A\x1b[3~\x1b[1~')
        t.until('0/q 退出', mark)
        self.assertNotIn('查看与更新 API 说明'.encode(), t.output[mark:])
        t.send(b'0\r')
        self.assertEqual(t.wait(timeout=5), 0)

    def test_escape_returns_parent_but_root_escape_stays(self):
        t = self.terminal()
        t.until('0/q 退出')
        mark = t.send(b'1\r')
        t.menu('安装推理与生成引擎', '返回', mark)
        mark = t.send(b'\x1b')
        t.menu('从这里开始', '退出', mark)
        mark = t.send(b'\x1b')
        t.menu('从这里开始', '退出', mark)
        self.assertIsNone(t.process.poll())
        self.assertNotIn('基础环境安装'.encode(), t.output)
        t.send(b'0\r')
        self.assertEqual(t.wait(timeout=5), 0)
        t.assert_restored(self)
        self.assertEqual(list(Path(t.home.name).iterdir()), [])

    def test_double_escape_and_alt_keys_do_not_leak_actions(self):
        for keys in (
            b'\x1b\x1b',
            b'\x1b\x1bq',
            b'\x1b\x1b4',
            b'\x1b\x1b[1;5A',
            b'\x1b\x1bO1;5D',
            b'\x1b[' + b'1;' * 80 + b'4q',
        ):
            with self.subTest(keys=keys):
                t = self.terminal()
                t.until('0/q 退出')
                mark = t.send(keys)
                t.until('0/q 退出', mark)
                self.assertIsNone(t.process.poll())
                mark = t.send(b'\r')
                t.menu('安装推理与生成引擎', '返回', mark)
                mark = t.send(b'\x1b')
                t.menu('从这里开始', '退出', mark)
                t.send(b'0\r')
                self.assertEqual(t.wait(timeout=5), 0)

    def test_complete_and_delayed_arrow_sequences_are_not_escape(self):
        for prefix in (b'[', b'O'):
            for delayed in (False, True):
                with self.subTest(prefix=prefix, delayed=delayed):
                    t = self.terminal()
                    t.until('0/q 退出')
                    mark = t.send(b'1\r')
                    t.menu('安装推理与生成引擎', '返回', mark)
                    mark = len(t.output)
                    for sequence in (b'\x1b' + prefix + b'B', b'\x1b' + prefix + b'1;5A'):
                        if delayed:
                            for byte in sequence:
                                t.send(bytes([byte]))
                                time.sleep(0.04)
                        else:
                            t.send(sequence)
                    t.until('0/q 返回', mark)
                    # 仅定位菜单项以同步输出，不执行操作。
                    mark = t.send(b'2')
                    t.until('0/q 返回', mark)
                    self.assertNotIn('从这里开始'.encode(), t.output[mark:])
                    self.assertIn('安装运行工具'.encode(), t.output[mark:])
                    mark = t.send(b'\x1b')
                    t.menu('从这里开始', '退出', mark)
                    t.send(b'0\r')
                    self.assertEqual(t.wait(timeout=5), 0)

    def test_locale_collation_does_not_swallow_arrow_enter(self):
        locales = subprocess.check_output(['locale', '-a'], text=True).splitlines()
        candidates = [name for name in locales if name.lower().replace('-', '') in ('en_us.utf8', 'zh_cn.utf8')]
        if not candidates:
            self.skipTest('排序回归需要非 C 的 UTF-8 语言环境')
        version = subprocess.check_output(['bash', '-c', 'echo "${BASH_VERSINFO[0]}"'], text=True)
        # Bash 3.2 使用语言环境排序；新版需关闭 ASCII 范围优化，
        # 才能复现旧解析器的问题。
        options = ('+O', 'globasciiranges') if int(version) >= 5 else ()
        t = self.terminal(columns=32, LC_ALL=candidates[0], bash_options=options)
        t.until('0/q 退出')
        mark = t.send(b'\x1b[B\r')
        t.menu('模型管理', '返回', mark)
        self.assertNotIn('安装推理与生成引擎'.encode(), t.output[mark:])
        mark = t.send(b'\x1b[D')
        t.until('0/q 退出', mark)
        frame = self.assert_screen_fits(t.output[mark:].decode(), 32, 24)
        self.assertIn('本地 AI 创作工坊', frame)
        self.assertIn('准备基础环境，选择推理与生成引擎', ''.join(frame.split()))
        mark = t.send(b'\x1b[A\x1b[A\x1b[C')
        t.menu('Llama benchmark', '返回', mark)
        frame = self.assert_screen_fits(t.output[mark:].decode(), 32, 24)
        self.assertIn('Benchmark', frame)
        self.assertIn('MLX-Serve 长上下文', frame)
        mark = t.send(b'\x1b')
        t.menu('从这里开始', '退出', mark)
        t.send(b'0\r')
        self.assertEqual(t.wait(timeout=5), 0)

    def test_benchmark_compatibility_description_fits_narrow_terminal(self):
        t = self.terminal(columns=32, rows=24)
        t.until('0/q 退出')
        mark = t.send(b'5\r')
        t.menu('Llama benchmark', '返回', mark)
        frame = self.assert_screen_fits(t.output[mark:].decode(), 32, 24)
        description = '0、50K、100K、200K 上下文吞吐测试；PP 计时不兼容 MLX-Serve、oMLX 等框架的空 role 消息。'
        self.assertIn(''.join(description.split()), ''.join(frame.split()))
        self.assertNotIn('适用于兼容接口', frame)
        t.send(b'0\r0\r')
        self.assertEqual(t.wait(timeout=5), 0)

    def test_interrupt_restores_terminal(self):
        t = self.terminal()
        t.until('0/q 退出')
        # Bash read 已进入无回显单键读取时发送中断。
        time.sleep(0.1)
        os.kill(t.process.pid, signal.SIGINT)
        self.assertEqual(t.wait(timeout=5), 130)
        t.assert_restored(self)

    def test_help_waits_before_clearing(self):
        t = self.terminal()
        t.until('0/q 退出')
        mark = t.send(b'h\r')
        t.until('Enter / Esc / ← 返回菜单', mark)
        self.assertIn(b'script-help', t.output[mark:])
        mark = t.send(b'\r')
        t.menu('从这里开始', '退出', mark)
        t.send(b'0\r')
        self.assertEqual(t.wait(timeout=5), 0)

    def test_tty_plain_modes_never_emit_ansi(self):
        for environment in ({'TERM': 'dumb'}, {'NO_COLOR': ''}):
            with self.subTest(environment=environment):
                t = self.terminal(**environment)
                t.until('请选择编号：')
                t.send(b'0\n')
                self.assertEqual(t.wait(timeout=5), 0)
                self.assertNotIn(b'\x1b', t.output)

    def assert_screen_fits(self, raw, columns, rows):
        frame = raw.split('\x1b[2J\x1b[H')[-1]
        plain = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', frame).replace('\r', '')
        widths = [
            sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in line)
            for line in plain.splitlines()
        ]
        self.assertLessEqual(max(widths), columns, plain)
        # Leave a row for the cursor: writing a newline on the last row scrolls.
        self.assertLess(len(plain.splitlines()), rows, plain)
        return plain

    def test_responsive_branding_and_screen_fit(self):
        sizes = [(columns, rows) for columns in (32, 40, 80, 100, 120) for rows in (24, 30)] + [(80, 18)]
        descriptions = (
            '准备基础环境，选择推理与生成引擎',
            '浏览、下载模型，设置存储目录',
            '在你指定的仓库目录中配置脚本和服务',
            '检查安装状态，整理本机接口说明',
        )
        for columns, rows in sizes:
            with self.subTest(columns=columns, rows=rows):
                t = self.terminal(columns=columns, rows=rows)
                t.until('0/q 退出')
                raw = t.output.decode()
                plain = self.assert_screen_fits(raw, columns, rows)
                logo_rows = 5 if columns >= 100 and rows >= 28 else (3 if columns >= 64 and rows >= 22 else 0)
                lines = plain.splitlines()
                self.assertEqual(sum('█' in line for line in lines), logo_rows)
                # Product and subtitle must be distinct lines, separated from
                # each other AND the artwork by real empty lines.
                product = lines.index('  Mac AI Kit')
                self.assertEqual(lines[product - 1], '')
                self.assertEqual(lines[product + 1], '')
                self.assertEqual(lines[product + 2], '  本地 AI 创作工坊')
                self.assertEqual(plain.count('Mac AI Kit'), 1)
                self.assertNotIn('本地智能工作站', plain)
                self.assertIn('\x1b[1;97;44m', raw)
                for code in re.findall(r'\x1b\[([0-9;]*)m', raw):
                    self.assertFalse(set(code.split(';')) & {'32', '35', '42', '45', '92', '95', '102', '105'})
                for index, description in enumerate(descriptions, 1):
                    mark = t.send(str(index).encode())
                    t.until('0/q 退出', mark)
                    frame = self.assert_screen_fits(t.output[mark:].decode(), columns, rows)
                    # Wrapping must never truncate focused descriptions.
                    self.assertIn(description.replace(' ', ''), ''.join(frame.split()))
                    self.assertEqual(len(re.findall(r'^ *[> ]? *[1-4]  ', frame, re.M)), 4)
                    self.assertIn('h 帮助', frame)
                    self.assertIn('数字定位', frame)
                    self.assertIn('Esc/← 返回', frame)
                mark = t.send(b'h')
                t.until('帮助（按 Enter 确认）', mark)
                self.assert_screen_fits(t.output[mark:].decode(), columns, rows)
                mark = t.send(b'0')
                t.until('退出（按 Enter 确认）', mark)
                self.assert_screen_fits(t.output[mark:].decode(), columns, rows)
                t.send(b'\r')
                self.assertEqual(t.wait(timeout=5), 0)
                t.assert_restored(self)

    def test_non_tty_plain_menu_and_cli_compatibility(self):
        result = subprocess.run(
            ['bash', str(ROOT / 'start.sh')],
            input='2\n0\n0\n',
            text=True,
            capture_output=True,
            timeout=5,
            env=dict(os.environ, TERM='xterm'),
        )
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('\x1b', result.stdout)
        self.assertIn('模型管理', result.stdout)
        self.assertIn('6  服务管理', result.stdout)
        for description in (
            '准备基础环境，选择推理与生成引擎',
            '浏览、下载模型，设置存储目录',
            '在你指定的仓库目录中配置脚本和服务',
        ):
            self.assertIn(description, result.stdout)
        result = subprocess.run(['bash', str(ROOT / 'start.sh'), '--help'], text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        for command in (
            'env',
            'capabilities',
            'download-client',
            'models',
            'download',
            'check',
            'api',
            'merge-h3',
            'remote',
        ):
            self.assertIn(command, result.stdout)


if __name__ == '__main__':
    unittest.main()
