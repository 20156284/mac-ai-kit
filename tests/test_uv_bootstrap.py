"""uv 下载客户端安装流程的离线模拟测试；不实际安装任何软件。"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/bootstrap_download_env.sh'


class UvBootstrapTests(unittest.TestCase):
    def run_mock(
        self, answer='yes\n', conflict=None, missing_python=False, fail_install=False, preview=False, details=False
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bins = root / 'bin'
            bins.mkdir()
            tools = root / 'uv tools'
            tools.mkdir()
            commands = root / 'user bin'
            commands.mkdir()
            log = root / 'uv.log'
            if conflict:
                (commands / conflict).write_text('原有文件，不执行')
            scripts = {
                'uname': 'echo Darwin',
                'id': 'echo 501',
                'uv': '''printf '%s\\n' "$*" >> "$MOCK_LOG"
case "$1 $2" in
 'tool dir') [ "${3:-}" = --bin ] && printf '%s\\n' "$MOCK_BIN" || printf '%s\\n' "$MOCK_TOOLS" ;;
 'python find') [ "$MOCK_MISSING" = 0 ] || exit 1; echo /usr/bin/python3 ;;
 'tool install') [ "$MOCK_FAIL" = 0 ] || exit 9; printf '#!/bin/sh\\nexit 99\\n' > "$MOCK_BIN/ms-hub"; chmod +x "$MOCK_BIN/ms-hub" ;;
 *) exit 88 ;;
esac''',
            }
            for name, body in scripts.items():
                p = bins / name
                p.write_text('#!/bin/sh\n' + body + '\n')
                p.chmod(0o755)
            env = dict(
                os.environ,
                PATH=str(bins) + ':/usr/bin:/bin',
                MOCK_LOG=str(log),
                MOCK_BIN=str(commands),
                MOCK_TOOLS=str(tools),
                MOCK_MISSING=str(int(missing_python)),
                MOCK_FAIL=str(int(fail_install)),
            )
            args = ['bash', str(SCRIPT)] + (['--dry-run'] if preview else []) + (['--details'] if details else [])
            result = subprocess.run(args, input=answer, text=True, capture_output=True, env=env)
            calls = log.read_text() if log.exists() else ''
            contents = sorted(p.name for p in commands.iterdir())
            return result, calls, contents

    def test_preview_never_invokes_uv(self):
        p, calls, files = self.run_mock(preview=True)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(calls, '')
        self.assertEqual(files, [])

    def test_explicit_confirmation_installs_isolated_pinned_tool(self):
        p, calls, files = self.run_mock()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(
            'tool install --python /usr/bin/python3 --no-python-downloads --default-index https://pypi.org/simple modelscope-hub==0.4.0',
            calls,
        )
        self.assertEqual(files, ['ms-hub'])
        self.assertNotIn('update-shell', calls)
        self.assertNotIn('--force', calls)
        self.assertIn('模型下载工具已安装', p.stdout)
        self.assertIn('--quiet', calls)
        self.assertNotIn('/usr/bin/python3', p.stdout)
        self.assertNotIn('user bin', p.stdout)
        self.assertNotIn('uv tools', p.stdout)
        self.assertNotIn('uv tool install', p.stdout)

    def test_details_show_paths_without_changing_install_safety(self):
        p, calls, files = self.run_mock(details=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('/usr/bin/python3', p.stdout)
        self.assertIn('user bin', p.stdout)
        self.assertIn('uv tools', p.stdout)
        self.assertIn('uv tool install', p.stdout)
        self.assertIn('--cli', p.stdout)
        self.assertNotIn('--quiet', calls)
        self.assertNotIn('update-shell', calls)
        self.assertNotIn('--force', calls)
        self.assertEqual(files, ['ms-hub'])

    def test_clear_affirmative_answers_are_accepted(self):
        for answer in ('y\n', 'Y\n', 'yes\n', 'YES\n', '是\n'):
            with self.subTest(answer=answer):
                p, calls, files = self.run_mock(answer=answer)
                self.assertEqual(p.returncode, 0)
                self.assertIn('tool install', calls)
                self.assertEqual(files, ['ms-hub'])

    def test_empty_answer_does_not_install(self):
        p, calls, files = self.run_mock(answer='\n')
        self.assertEqual(p.returncode, 0)
        self.assertNotIn('tool install', calls)
        self.assertEqual(files, [])

    def test_decline_never_installs(self):
        p, calls, files = self.run_mock(answer='no\n')
        self.assertEqual(p.returncode, 0)
        self.assertNotIn('tool install', calls)
        self.assertEqual(files, [])

    def test_all_console_script_conflicts_block_overwrite(self):
        for name in ('ms-hub', 'modelscope-hub', 'ms', 'modelscope'):
            with self.subTest(name=name):
                p, calls, files = self.run_mock(conflict=name)
                self.assertNotEqual(p.returncode, 0)
                self.assertNotIn('tool install', calls)
                self.assertEqual(files, [name])

    def test_missing_local_python_does_not_download(self):
        p, calls, files = self.run_mock(missing_python=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('python find --no-python-downloads >=3.10', calls)
        self.assertNotIn('tool install', calls)
        self.assertEqual(files, [])

    def test_failed_install_does_not_claim_success(self):
        p, calls, files = self.run_mock(fail_install=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn('模型下载工具已安装', p.stdout)
        self.assertEqual(files, [])

    def test_help_and_invalid_option(self):
        p = subprocess.run(['bash', str(SCRIPT), '--help'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn('安装模型下载工具', p.stdout)
        p = subprocess.run(['bash', str(SCRIPT), '--unknown'], capture_output=True)
        self.assertEqual(p.returncode, 2)


if __name__ == '__main__':
    unittest.main()
