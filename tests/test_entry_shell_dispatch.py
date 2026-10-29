"""真实 shell 入口回归；只读帮助、退出导航及临时 mock，不安装或联网。"""

import os
import errno
from pathlib import Path
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import termios
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHELLS = ['/bin/bash', '/bin/sh'] + (['/usr/bin/zsh'] if Path('/usr/bin/zsh').exists() else [])


class EntryShellDispatchTests(unittest.TestCase):
    def run_entry(self, shell, *args, input=''):
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                [shell, str(ROOT / 'start.sh'), *args],
                input=input,
                text=True,
                capture_output=True,
                timeout=10,
                env=dict(os.environ, HOME=home),
            )
            self.assertEqual(list(Path(home).iterdir()), [])
            return result

    def test_help_and_piped_exit(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                result = self.run_entry(shell, '--help')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Mac AI Kit', result.stdout)
                result = self.run_entry(shell, input='0\n')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('bad option', result.stderr)

    def test_direct_executable(self):
        if not os.access(ROOT / 'start.sh', os.X_OK):
            self.skipTest('入口未设置可执行位')
        result = subprocess.run([str(ROOT / 'start.sh'), '--help'], text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_source_returns_without_parent_mutation(self):
        for shell in SHELLS:
            for wrapper in (False, True):
                with self.subTest(shell=shell, wrapper=wrapper), tempfile.TemporaryDirectory() as tmp:
                    # 调用前后检查变量、选项和信号处理；source 返回后父 shell 必须仍存活。
                    script = '''ROOT=sentinel; SCRIPTS=sentinel; MODEL_DIR=sentinel
trap 'printf parent_trap' INT
before_options=$(set +o); before_traps=$(trap); before_path=$PATH
. "$1" --help
result=$?
[ "$result" = 2 ] || exit 51
[ "$ROOT:$SCRIPTS:$MODEL_DIR" = sentinel:sentinel:sentinel ] || exit 52
[ "$PATH" = "$before_path" ] || exit 53
[ "$(set +o)" = "$before_options" ] || exit 54
[ "$(trap)" = "$before_traps" ] || exit 55
printf '父 shell 仍在运行\\n'
'''
                    if wrapper:
                        path = Path(tmp) / 'wrapper.sh'
                        path.write_text(script)
                        command = [shell, str(path), str(ROOT / 'start.sh')]
                    else:
                        command = [shell, '-c', script, shell, str(ROOT / 'start.sh')]
                    result = subprocess.run(command, text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('父 shell 仍在运行', result.stdout)
                    self.assertIn('请不要 source', result.stderr)

    def test_spaces_arguments_and_child_exit_status(self):
        with tempfile.TemporaryDirectory(prefix='工具包 with spaces ') as tmp:
            root = Path(tmp)
            shutil.copy2(ROOT / 'start.sh', root / 'start.sh')
            (root / 'scripts').mkdir()
            shutil.copy2(ROOT / 'scripts/path_validation.sh', root / 'scripts/path_validation.sh')
            shutil.copy2(ROOT / 'scripts/model_directory.sh', root / 'scripts/model_directory.sh')
            (root / 'remote').mkdir()
            (root / 'remote/start-remotion-remote.sh').write_text('#!/bin/bash\nprintf "<%s>\\n" "$@"\nexit 37\n')
            for shell in SHELLS:
                with self.subTest(shell=shell):
                    result = subprocess.run(
                        [shell, str(root / 'start.sh'), 'remote', '--project', "/a b/中文 O'Brien *"],
                        text=True,
                        capture_output=True,
                        timeout=10,
                    )
                    self.assertEqual(result.returncode, 37, result.stderr)
                    self.assertEqual(result.stdout, "<--configure>\n<--project>\n</a b/中文 O'Brien *>\n")
                    result = subprocess.run(
                        [shell, str(root / 'start.sh'), 'remote', '--project', "/a b/中文 O'Brien *", '--status'],
                        text=True,
                        capture_output=True,
                        timeout=10,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, '')

    def test_nonbash_dispatch_uses_path_bash(self):
        for shell in SHELLS:
            if shell == '/bin/bash':
                continue
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                marker = root / 'selected'
                wrapper = root / 'bash'
                wrapper.write_text('#!/bin/sh\nprintf chosen > "$BASH_MARKER"\nexec /bin/bash "$@"\n')
                wrapper.chmod(0o755)
                environment = dict(
                    os.environ,
                    PATH=tmp + os.pathsep + os.environ['PATH'],
                    BASH_MARKER=str(marker),
                    BASH_VERSION='inherited-not-bash',
                )
                result = subprocess.run(
                    [shell, str(ROOT / 'start.sh'), '--help'],
                    text=True,
                    capture_output=True,
                    timeout=10,
                    env=environment,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                shell_is_bash = (
                    subprocess.run(
                        [shell, '-c', 'test -n "$BASH_VERSION"'],
                        env=dict(environment, BASH_VERSION=''),
                        capture_output=True,
                    ).returncode
                    == 0
                )
                if not shell_is_bash:
                    self.assertEqual(marker.read_text(), 'chosen')
                self.assertIn('Mac AI Kit', result.stdout)

    def test_inherited_zsh_identity_does_not_replace_actual_bash(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / 'bash'
            wrapper.write_text('#!/bin/sh\nexit 91\n')
            wrapper.chmod(0o755)
            environment = dict(
                os.environ,
                PATH=tmp + os.pathsep + os.environ['PATH'],
                ZSH_VERSION='inherited-zsh',
                ZSH_EVAL_CONTEXT='toplevel:file',
            )
            result = subprocess.run(
                ['/bin/bash', str(ROOT / 'start.sh'), '--help'],
                text=True,
                capture_output=True,
                timeout=10,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Mac AI Kit', result.stdout)
            result = subprocess.run(
                [
                    '/bin/bash',
                    '-c',
                    '. "$1" --help; result=$?; printf "parent:%s\\n" "$result"',
                    'parent',
                    str(ROOT / 'start.sh'),
                ],
                text=True,
                capture_output=True,
                timeout=10,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('parent:2', result.stdout)
            self.assertIn('请不要 source', result.stderr)

    def test_no_path_bash_falls_back_to_system_bash(self):
        if '/usr/bin/zsh' not in SHELLS:
            self.skipTest('zsh unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            # 帮助仍需 dirname 和 cat；这里只刻意移除 PATH 中的 bash。
            for name in ('dirname', 'cat'):
                os.symlink(shutil.which(name), Path(tmp) / name)
            result = subprocess.run(
                ['/usr/bin/zsh', str(ROOT / 'start.sh'), '--help'],
                text=True,
                capture_output=True,
                timeout=10,
                env=dict(os.environ, PATH=tmp),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Mac AI Kit', result.stdout)

    def test_real_shell_pty_arrows_and_terminal_restore(self):
        for shell in SHELLS:
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as home:
                master, slave = pty.openpty()
                before = termios.tcgetattr(slave)
                environment = dict(os.environ, HOME=home, TERM='xterm-256color')
                environment.pop('NO_COLOR', None)
                process = subprocess.Popen(
                    [shell, str(ROOT / 'start.sh')],
                    stdin=slave,
                    stdout=slave,
                    stderr=slave,
                    start_new_session=True,
                    env=environment,
                )
                output = bytearray()

                def until(text):
                    deadline = time.monotonic() + 7
                    start = len(output)
                    while text.encode() not in output[start:]:
                        if time.monotonic() >= deadline:
                            self.fail(output.decode(errors='replace'))
                        if select.select([master], [], [], 0.1)[0]:
                            output.extend(os.read(master, 65536))

                def wait_draining(timeout):
                    deadline = time.monotonic() + timeout
                    while process.poll() is None and time.monotonic() < deadline:
                        if select.select([master], [], [], 0.1)[0]:
                            try:
                                output.extend(os.read(master, 65536))
                            except OSError:
                                pass
                    if process.poll() is None:
                        self.fail(output.decode(errors='replace'))
                    return process.returncode

                try:
                    until('0/q 退出')
                    os.write(master, b'\x1b[B\r')
                    until('模型管理')
                    os.write(master, b'\x1b[D')
                    until('从这里开始')
                    os.write(master, b'0\r')
                    self.assertEqual(wait_draining(7), 0)
                    try:
                        after = termios.tcgetattr(slave)
                    except termios.error as exc:
                        if sys.platform != 'darwin' or exc.args[0] != errno.ENOTTY:
                            raise
                    else:
                        self.assertEqual(before, after)
                    self.assertNotIn(b'bad option', output)
                    self.assertNotIn(b'ui_select:read', output)
                    self.assertEqual(list(Path(home).iterdir()), [])
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
                    os.close(master)
                    os.close(slave)


if __name__ == '__main__':
    unittest.main()
