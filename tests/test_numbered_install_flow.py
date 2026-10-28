"""使用真实选择器与无操作安装替身，检查选择和取消路径。"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class NumberedFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.home = self.work / 'home'; self.home.mkdir()
        self.kit = self.work / 'kit'; self.kit.mkdir()
        self.scripts = self.kit / 'scripts'; self.scripts.mkdir()
        self.bin = self.work / 'bin'; self.bin.mkdir()
        self.log = self.work / 'mutations'
        for name in ('start.sh', 'catalog.json'):
            shutil.copy2(ROOT / name, self.kit / name)
        for name in ('setup_ai_capabilities.sh', 'download_models.py', 'terminal_ui.sh', 'path_validation.sh', 'path_validation.py', 'model_directory.sh'):
            shutil.copy2(ROOT / 'scripts' / name, self.scripts / name)
        for name in ('dirname', 'cat', 'mkdir', 'mktemp', 'mv', 'rm'):
            (self.bin / name).symlink_to(shutil.which(name))
        for name in ('setup_mac_environment.sh', 'bootstrap_download_env.sh'):
            self.write(self.scripts / name, 'echo MUTATION >> "$LOG"; exit 90')
        self.write(self.bin / 'python3', 'exit 1')
        (self.scripts / 'api_inventory.py').write_text('raise RuntimeError("UNEXPECTED API WRITE")')
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / '.config'), PATH=str(self.bin), LOG=str(self.log))

    def write(self, path, body):
        path.write_text('#!/bin/bash\n' + body + '\n'); path.chmod(0o755)

    def run_entry(self, data='', args=()):
        return subprocess.run(['/bin/bash', str(self.kit / 'start.sh'), *args], input=data,
                              env=self.env, text=True, capture_output=True)

    def choose(self, data='', *args):
        return subprocess.run(['/bin/bash', str(self.scripts / 'setup_ai_capabilities.sh'), '--choose-only', *args],
                              input=data, env=self.env, text=True, capture_output=True)

    def test_numbers_spaces_commas_deduplicate_and_invalid_retry(self):
        result = self.choose('99\n1, 2 5 1\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'mlx-serve,llama.cpp,audio.cpp')
        self.assertIn('选择无效', result.stderr)
        self.assertIn('Qwen 转写 / YuE2 音乐', result.stderr)
        self.assertNotIn('用法：', result.stderr)
        self.assertNotIn('--root', result.stderr)
        self.assertFalse(self.log.exists())

    def test_glob_is_never_expanded_into_choices(self):
        for number in range(1, 7):
            (self.work / str(number)).write_text('')
        for value in ('*', '[1-6]'):
            result = subprocess.run(['/bin/bash', str(self.scripts / 'setup_ai_capabilities.sh'), '--choose-only', '--select', value],
                                    cwd=self.work, env=self.env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(result.stdout, '')
            self.assertFalse(self.log.exists())

    def test_all_and_help_are_explicit(self):
        result = self.choose('all\n')
        self.assertEqual(len(result.stdout.strip().split(',')), 5)
        self.assertNotIn('llama-swap', result.stdout)
        self.assertNotIn('用法：', result.stderr)
        result = self.choose('h\n0\n')
        self.assertIn('用法：', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_selection_prompt_explains_all_and_blank(self):
        result = self.choose('\n')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        for text in ('输入 all 选择常用组件', '1,2,5', '空白回车或 0 返回', '下一步显示安装清单'):
            self.assertIn(text, result.stderr)
        self.assertFalse(self.log.exists())

    def test_cancel_before_runtime_directory_and_api(self):
        (self.home / 'AI').mkdir()
        for data in ('1\n2\n0\n0\n0\n', '1\n2\n\n0\n0\n', '1\n2\n1\nn\n0\n0\n', '1\n2\n99\n0\n0\n0\n'):
            result = self.run_entry(data)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('基础环境安装', result.stdout)
            self.assertNotIn('用法：', result.stdout + result.stderr)
            self.assertFalse(self.log.exists())
            self.assertEqual(list((self.home / 'AI').iterdir()), [])

    def test_invalid_explicit_selection_does_not_prepare_runtime(self):
        result = self.run_entry(args=('capabilities', '--install', '--select', 'invalid'))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.log.exists())

    def test_read_only_found_missing_and_broken_states_do_not_run_tools(self):
        self.write(self.bin / 'llama-server', 'echo EXECUTED >> "$LOG"')
        bins = self.home / 'AI/bin'; bins.mkdir(parents=True)
        (bins / 'sd-server').symlink_to('/nonexistent/kit-test')
        app = self.home / 'Applications/MLX-Serve.app'; app.mkdir(parents=True)
        result = self.choose('0\n')
        self.assertIn('App 已找到，保留', result.stderr)
        self.assertIn('已找到，保留（未运行核验）', result.stderr)
        self.assertIn('已有入口异常', result.stderr)
        self.assertIn('未找到，按需安装', result.stderr)
        self.assertFalse(self.log.exists())

    def test_confirmation_discloses_settings_dependencies_without_mutation(self):
        result = subprocess.run(['/bin/bash', str(self.scripts / 'setup_ai_capabilities.sh'), '--confirm-only', '--models-dir', str(self.home / 'models')],
                                input='1 6\nn\n', env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        for text in ('备份旧设置', 'Node/工具链', 'shell PATH', '[y/N]'):
            self.assertIn(text, result.stderr)
        self.assertFalse((self.home / 'models').exists())

    def test_preview_never_mutates(self):
        result = self.run_entry(args=('capabilities', '--preview'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_existing_client_skips_environment_setup(self):
        self.write(self.bin / 'ms-hub', 'echo EXECUTED >> "$LOG"')
        result = self.run_entry(args=('download-client', '--install'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())

    def test_model_cancel_and_invalid_selection_before_client_setup(self):
        self.write(self.bin / 'python3', 'exec ' + shlex.quote(sys.executable) + ' "$@"')
        for data in ('2\n1\n\n0\n0\n0\n', '2\n1\n\n9999\n0\n0\n0\n'):
            result = self.run_entry(data)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(self.log.exists())
            self.assertFalse((self.home / 'AI').exists())
            self.assertEqual((self.home / '.config/mac-ai-kit/model-directory').read_text().strip(), str(self.home / 'AI/models'))
        result = self.run_entry(args=('download', '--select', 'invalid'))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

if __name__ == '__main__': unittest.main()
