"""Bash 单入口离线回归；安装器与网络均替换为本地 mock。"""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lean kit O'Brien ")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.kit = self.work / 'copied kit'
        self.scripts = self.kit / 'scripts'
        self.scripts.mkdir(parents=True)
        shutil.copy2(ROOT / 'start.sh', self.kit / 'start.sh')
        shutil.copy2(ROOT / 'scripts/terminal_ui.sh', self.scripts / 'terminal_ui.sh')
        shutil.copy2(ROOT / 'scripts/path_validation.sh', self.scripts / 'path_validation.sh')
        shutil.copy2(ROOT / 'scripts/model_directory.sh', self.scripts / 'model_directory.sh')
        shutil.copy2(ROOT / 'scripts/path_validation.py', self.scripts / 'path_validation.py')
        self.bin = self.work / 'bin'
        self.bin.mkdir()
        self.home = self.work / 'home'
        self.home.mkdir()
        self.log = self.work / 'calls'
        for utility in ('dirname', 'cat', 'mkdir', 'mktemp', 'mv', 'rm'):
            (self.bin / utility).symlink_to(shutil.which(utility))
        self.env = dict(
            os.environ,
            HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / '.config'),
            PATH=str(self.bin),
            LOG=str(self.log),
        )
        self.script('python3', 'exit 1', self.bin)
        self.script('setup_mac_environment.sh', 'printf "env\\n" >> "$LOG"')
        self.script(
            'setup_ai_capabilities.sh',
            'if [ "$1" = --confirm-only ]; then echo mlx-serve; exit 0; fi; printf "caps\\n" >> "$LOG"; printf "%s\\n" "$@" >> "$LOG"',
        )
        self.script('bootstrap_download_env.sh', 'printf "client\\n" >> "$LOG"')
        (self.scripts / 'download_models.py').write_text(
            'import sys\nif "--choose-only" in sys.argv: print(sys.argv[sys.argv.index("--select")+1] if "--select" in sys.argv else "mock-model")\nelse: print("DOWNLOAD", repr(sys.argv[1:]))\n'
        )
        (self.scripts / 'merge_h3_models.py').write_text('import sys\nprint("MERGE", repr(sys.argv[1:]))\n')

    def script(self, name, body, folder=None):
        target = (folder or self.scripts) / name
        target.write_text('#!/bin/bash\n' + body + '\n')
        target.chmod(0o755)
        return target

    def run_entry(self, args=(), data=''):
        return subprocess.run(
            ['/bin/bash', str(self.kit / 'start.sh'), *args],
            input=data,
            cwd=self.work,
            env=self.env,
            text=True,
            capture_output=True,
        )

    def ready_runtime(self):
        self.script('python3', 'exec ' + shlex.quote(sys.executable) + ' "$@"', self.bin)
        self.script(
            'uv',
            'case "$1 $2" in "python find") printf "%s\\n" '
            + shlex.quote(sys.executable)
            + ' ;; "tool dir") printf "%s\\n" "$HOME/.local/bin" ;; esac',
            self.bin,
        )

    def test_api_scan_never_prepares_runtime(self):
        self.ready_runtime()
        (self.bin / 'uv').unlink()
        (self.scripts / 'api_inventory.py').write_text('import sys\nprint("INVENTORY", repr(sys.argv[1:]))\n')
        result = self.run_entry(['api', '--directory', str(self.home)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('INVENTORY', result.stdout)
        self.assertFalse(self.log.exists())

    def test_install_download_hooks_and_preview(self):
        self.ready_runtime()
        self.script('ms-hub', 'exit 0', self.bin)
        (self.home / 'AI').mkdir()
        models = self.home / 'AI/models'
        models.mkdir()
        (self.scripts / 'api_inventory.py').write_text('import sys\nprint("INVENTORY", repr(sys.argv[1:]))\n')
        for args in (
            ['capabilities', '--install', '--select', 'audio.cpp'],
            ['download', '--models-dir', str(models), '--select', 'x'],
        ):
            result = self.run_entry(args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('INVENTORY', result.stdout)
            self.assertIn('--if-present', result.stdout)
        for args in (['capabilities'], ['download', '--preview', '--models-dir', str(models)]):
            result = self.run_entry(args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('INVENTORY', result.stdout)
        (self.scripts / 'download_models.py').write_text(
            'import sys\nif "--choose-only" in sys.argv: print("mock-model")\nelse: raise SystemExit(7)\n'
        )
        result = self.run_entry(['download', '--models-dir', str(models), '--select', 'x'])
        self.assertEqual(result.returncode, 7)
        self.assertIn('INVENTORY', result.stdout)  # 报告部分文件，不吞下载失败退出码。

    def test_help_menu_and_env_without_python(self):
        for args in (['--help'], ['env'], ['check'], ['capabilities'], ['download-client']):
            result = self.run_entry(args)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((ROOT / 'manage.py').exists())
        result = self.run_entry(data='')
        self.assertEqual(result.returncode, 0)
        self.assertIn('输入已结束', result.stdout)

    def test_only_root_shell_entry_is_start(self):
        entries = sorted(p.name for p in ROOT.iterdir() if p.suffix in ('.sh', '.bash', '.command'))
        self.assertEqual(entries, ['start.sh'])
        self.assertEqual((ROOT / 'README.md').read_text().count('```bash\nbash start.sh\n```'), 1)

    def test_help_uses_terminal_bash_entry(self):
        result = self.run_entry(['--help'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('bash start.sh', result.stdout)
        self.assertNotIn('双击', result.stdout)

    def test_removed_mlx_modes_do_not_install(self):
        for mode in ('cli', 'gui'):
            result = self.run_entry(['capabilities', '--mlx-mode', mode])
            self.assertEqual(result.returncode, 2)
        self.assertFalse(self.log.exists())

    def test_default_capabilities_has_no_mlx_mode_flag(self):
        result = self.run_entry(['capabilities'])
        self.assertEqual(result.returncode, 0)
        self.assertIn('--dry-run', self.log.read_text())
        self.assertNotIn('--mlx-mode', self.log.read_text())

    def test_install_creates_shared_directory_but_preview_does_not(self):
        self.ready_runtime()
        directory = self.home / 'AI/model folder'
        result = self.run_entry(['capabilities', '--models-dir', str(directory)])
        self.assertEqual(result.returncode, 0)
        self.assertFalse(directory.exists())
        result = self.run_entry(['capabilities', '--install', '--select', 'mlx-serve', '--models-dir', str(directory)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(directory.is_dir())

    def test_install_rejects_symlink_or_missing_volume(self):
        self.ready_runtime()
        link = self.home / 'linked'
        link.symlink_to(self.home, target_is_directory=True)
        for directory in (str(link / 'models'), '/Volumes/lean-nonexistent-test-drive/models', str(self.home / '..')):
            result = self.run_entry(['capabilities', '--install', '--models-dir', directory])
            self.assertEqual(result.returncode, 2 if directory.endswith('/..') else 1, result.stderr)
        self.assertFalse(self.log.exists())

    def server_fixture(self, port_busy=False, exit_code=0):
        model = self.home / "AI/models/model O'Brien"
        model.mkdir(parents=True)
        (model / 'config.json').write_text('{}')
        folder = self.home / 'AI/bin'
        folder.mkdir(parents=True)
        self.script('mlx-serve', 'printf "%s\\n" "$@" > "$LOG"; exit ' + str(exit_code), folder)
        self.script('lsof', 'exit ' + ('0' if port_busy else '1'), self.bin)
        return model

    def test_removed_service_command_never_launches(self):
        model = self.server_fixture()
        for args in ([], ['--model', str(model)], ['--model', str(model), '--port', '12345']):
            result = self.run_entry(['serve', *args])
            self.assertEqual(result.returncode, 2)
            self.assertIn('未知任务：serve', result.stderr)
        self.assertFalse(self.log.exists())

    def test_removed_hidden_service_shortcut_never_launches(self):
        self.server_fixture()
        result = self.run_entry(data='9\n0\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('请选择菜单中的编号', result.stdout)
        self.assertFalse(self.log.exists())

    def test_h3_reuse_parameter_preserves_path_with_spaces(self):
        self.ready_runtime()
        source = self.work / "old H3 O'Brien"
        source.mkdir()
        result = self.run_entry(
            ['download', '--dry-run', '--select', 'h3-ref2va-mlx-8bit', '--reuse-from', str(source)]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--reuse-from', result.stdout)
        self.assertIn(str(source), result.stdout)
        self.assertFalse(self.log.exists())

    def test_h3_merge_menu_uses_current_directory_without_prompts(self):
        self.ready_runtime()
        directory = self.home / "AI/models O'Brien"
        result = self.run_entry(data=f'd\n{directory}\n2\n4\n0\n0\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('MERGE', result.stdout)
        self.assertIn(str(directory), result.stdout)
        self.assertIn('--models-dir', result.stdout)
        self.assertNotIn('--reuse-from', result.stdout)
        self.assertNotIn('来源目录', result.stdout)
        self.assertNotIn('目标版本', result.stdout)
        self.assertEqual(result.stdout.count('（输入 q 取消）'), 1)
        self.assertFalse(directory.exists())
        self.assertFalse(self.log.exists())

    def test_model_directory_trims_menu_and_cli_without_extra_models(self):
        self.ready_runtime()
        directory = self.home / 'AI/My models'
        directory.mkdir(parents=True)
        for value in (' ' + str(directory) + ' ', '\u00a0\t' + str(directory) + '\u00a0'):
            result = self.run_entry(data=f'd\n{value}\n2\n4\n0\n0\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(repr(str(directory)), result.stdout)
            self.assertNotIn('首尾含空白', result.stderr)
            result = self.run_entry(['download', '--dry-run', '--models-dir', value])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(repr(str(directory)), result.stdout)
        self.assertFalse((directory / 'models').exists())

    def test_blank_cli_directory_rejected_before_dependencies(self):
        result = self.run_entry(['download', '--models-dir', ' \t\u00a0'])
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.log.exists())

    def test_blank_menu_directory_uses_default(self):
        self.ready_runtime()
        result = self.run_entry(data='d\n \t\u00a0\n2\n4\n0\n0\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(repr(str(self.home / 'AI/models')), result.stdout)

    def test_h3_merge_advanced_arguments_are_forwarded(self):
        self.ready_runtime()
        directory = self.work / "H3 O'Brien"
        result = self.run_entry(['merge-h3', '--models-dir', str(directory), '--dry-run'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(directory), result.stdout)
        self.assertIn('--dry-run', result.stdout)
        self.assertFalse(self.log.exists())

    def test_h3_reuse_invalid_source_or_task_never_installs(self):
        result = self.run_entry(['download', '--select', 'h3-mlx-8bit', '--reuse-from', str(self.work / 'missing')])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.run_entry(['env', '--reuse-from', str(self.home)]).returncode, 2)
        self.assertFalse(self.log.exists())

    def test_paths_and_arguments_are_literal(self):
        self.ready_runtime()
        value = "relative O'Brien models;echo bad"
        result = self.run_entry(['download', '--dry-run', '--select', '1', '--models-dir', value])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.work / value), result.stdout)
        self.assertFalse((self.work / value).exists())

    def test_fresh_runtime_detected_after_install(self):
        # PATH initially has neither working Python nor uv. Installer creates a uv
        # command in a newly-created ~/.local/bin and exposes a managed Python.
        body = '/bin/mkdir -p "$HOME/.local/bin"\n'
        uv = (
            '#!/bin/bash\ncase "$1 $2" in "python find") printf "%s\\n" '
            + shlex.quote(sys.executable)
            + ' ;; "tool dir") printf "%s\\n" "$HOME/.local/bin" ;; esac\n'
        )
        body += (
            'printf %s '
            + shlex.quote(uv)
            + ' > "$HOME/.local/bin/uv"\n/bin/chmod +x "$HOME/.local/bin/uv"\nprintf "env\\n" >> "$LOG"'
        )
        self.script('setup_mac_environment.sh', body)
        self.script(
            'bootstrap_download_env.sh',
            'printf "client\\n" >> "$LOG"\nprintf "#!/bin/bash\\nexit 0\\n" > "$HOME/.local/bin/ms-hub"\n/bin/chmod +x "$HOME/.local/bin/ms-hub"',
        )
        result = self.run_entry(['download', '--select', '1', '--models-dir', 'model folder'])
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.log.read_text().splitlines(), ['env', 'client'])
        self.assertIn('DOWNLOAD', result.stdout)
        self.assertIn(str(self.home / '.local/bin/ms-hub'), result.stdout)

    def test_cancelled_environment_does_not_run_dependent(self):
        result = self.run_entry(['download', '--select', '1'])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.log.read_text().splitlines(), ['env'])
        self.assertNotIn('DOWNLOAD', result.stdout)

    def test_failed_environment_propagates(self):
        self.script('setup_mac_environment.sh', 'exit 23')
        result = self.run_entry(['download', '--select', '1'])
        self.assertEqual(result.returncode, 23)
        self.assertNotIn('DOWNLOAD', result.stdout)

    def test_cancelled_client_never_downloads(self):
        self.ready_runtime()
        result = self.run_entry(['download', '--select', '1'])
        self.assertEqual(result.returncode, 1)
        self.assertIn('未开始下载模型', result.stderr)
        self.assertNotIn('DOWNLOAD', result.stdout)

    def test_existing_client_skips_install(self):
        self.ready_runtime()
        self.script('ms-hub', 'exit 0', self.bin)
        result = self.run_entry(['download', '--select', '1'])
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_directory_is_shared_and_asked_once(self):
        self.ready_runtime()
        self.script('ms-hub', 'exit 0', self.bin)
        result = self.run_entry(data="d\nrelative model folder\n2\n1\n1\n0\n0\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('（输入 q 取消）'), 1)
        self.assertEqual(result.stdout.count('DOWNLOAD'), 2)
        self.assertIn(str(self.work / 'relative model folder'), result.stdout)

    def test_cancel_directory_does_not_prepare_dependencies(self):
        result = self.run_entry(data='2\n1\nq\n0\n0\n')
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_menu_failure_returns_menu_but_preserves_exit_status(self):
        self.script('setup_mac_environment.sh', 'exit 9')
        result = self.run_entry(data='1\n1\n0\n0\n')
        self.assertEqual(result.returncode, 9)
        self.assertIn('本次未完成', result.stdout)

    def test_mtp_explicit_source_preserves_legacy_whitespace(self):
        self.ready_runtime()
        source = self.home / 'models ' / 'text/mtp'
        source.mkdir(parents=True)
        result = self.run_entry(['download', '--dry-run', '--select', 'mtp', '--reuse-mtp-source', str(source)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--reuse-mtp-source', result.stdout)
        self.assertIn(str(source), result.stdout)
        self.assertTrue(source.is_dir())
        self.assertFalse(self.log.exists())
        self.assertEqual(self.run_entry(['env', '--reuse-mtp-source', str(source)]).returncode, 2)

    def test_bad_arguments_do_not_install(self):
        for args in (
            ['bad'],
            ['env', '--yes'],
            ['models', '--install'],
            ['env', '--install', '--dry-run'],
            ['download', '--models-dir', '/'],
            ['download', '--select'],
        ):
            self.assertEqual(self.run_entry(args).returncode, 2, args)
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()
