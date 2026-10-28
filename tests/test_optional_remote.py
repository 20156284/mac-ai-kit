"""Root dispatch only: inert remote fixture, no deployment or service installation."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class OptionalRemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.kit = self.work / "kit with spaces"; self.kit.mkdir()
        shutil.copy2(ROOT / 'start.sh', self.kit / 'start.sh')
        (self.kit / 'scripts').mkdir()
        shutil.copy2(ROOT / 'scripts/terminal_ui.sh', self.kit / 'scripts/terminal_ui.sh')
        shutil.copy2(ROOT / 'scripts/path_validation.sh', self.kit / 'scripts/path_validation.sh')
        shutil.copy2(ROOT / 'scripts/model_directory.sh', self.kit / 'scripts/model_directory.sh')
        shutil.copy2(ROOT / 'scripts/path_validation.py', self.kit / 'scripts/path_validation.py')
        self.home = self.work / 'home'; self.home.mkdir()
        self.log = self.work / 'calls'
        self.remote = self.kit / 'remote'; self.remote.mkdir()
        self.launcher = self.remote / 'start-remotion-remote.sh'
        # It is intentionally not a functional service. Record argc and exact args.
        self.launcher.write_text('''#!/bin/bash
printf 'CALL:%s\\n' "$#" >> "$LOG"
printf '<%s>\\n' "$@" >> "$LOG"
case "${1:-}" in --invalid) exit 2 ;; esac
exit 0
''')
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / '.config'), LOG=str(self.log))

    def run_entry(self, args=(), data=''):
        return subprocess.run(['/bin/bash', str(self.kit / 'start.sh'), *args],
                              env=self.env, input=data, text=True, capture_output=True, timeout=5)

    def test_uppercase_menu_dispatches_once(self):
        result = self.run_entry(data='R\n0\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text(), 'CALL:1\n<--configure>\n')
        self.assertIn('配置 Remotion 远程渲染', result.stdout)
        self.assertNotIn('r 复用已有 H3', result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_cli_preserves_argument_boundaries(self):
        result = self.run_entry(('remote', '--project', "/a dir/with 'quote"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text(), "CALL:3\n<--configure>\n<--project>\n</a dir/with 'quote>\n")

    def test_script_help_explains_configuration_without_dispatch(self):
        result = self.run_entry(('script-help', 'remote'))
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        self.assertIn('仅保存配置，不启动服务', result.stdout)

    def test_idle_help_quit_unknown_do_not_call_remote(self):
        for args, data, code in [((), '0\n', 0), (('--help',), '', 0),
                                 (('unknown',), '', 2), (('env', '--invalid'), '', 2),
                                 (('script-help', 'remote', '--install'), '', 2)]:
            result = self.run_entry(args, data)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertFalse(self.log.exists())
            self.assertEqual(list(self.home.iterdir()), [])

    def test_runtime_and_unknown_remote_args_never_dispatch(self):
        for args in (('--invalid',), ('--status',), ('--start',), ('--project',),
                     ('--project', ''), ('--project', '/some project', '--status')):
            with self.subTest(args=args):
                result = self.run_entry(('remote', *args))
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.log.exists())
                self.assertEqual(list(self.home.iterdir()), [])

    def test_missing_module_fails_without_fetching(self):
        self.launcher.unlink()
        result = self.run_entry(('remote',))
        self.assertEqual(result.returncode, 1)
        self.assertIn('Remote 模块文件缺失', result.stderr)
        self.assertFalse(self.log.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_six_component_all_still_excludes_remote(self):
        result = subprocess.run(['/bin/bash', str(ROOT / 'scripts/setup_ai_capabilities.sh'),
                                 '--choose-only', '--select', 'all'], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        selected = result.stdout.strip().split(',')
        self.assertEqual(len(selected), 5)
        self.assertNotIn("llama-swap", selected)
        self.assertFalse(any('remote' in item.lower() or 'remotion' in item.lower() for item in selected))
        self.assertFalse(self.log.exists())

if __name__ == '__main__':
    unittest.main()
