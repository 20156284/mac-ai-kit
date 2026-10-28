"""模型根每级路径空白防护；全部使用本地微型临时文件。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from path_validation import validate_model_root


class ModelRootPathTests(unittest.TestCase):
    def test_reject_each_component_and_offer_explicit_suggestion(self):
        for value in ('/Volumes/ Samsung1TB/AI/models',
                      '/tmp/a\t/models', '/tmp/\u3000a/models', '/tmp/models /'):
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                validate_model_root(value)
            self.assertIn(repr(value), str(caught.exception))
            self.assertIn('显式重新选择', str(caught.exception))
            self.assertIn('不会自动改名', str(caught.exception))

    def test_trim_overall_unicode_whitespace(self):
        for whitespace in (' ', '\t', '\u00a0', '\u3000', '\n', '\r', '\u2009', ' \t\u00a0'):
            value = whitespace + '/tmp/My SSD/models' + whitespace
            self.assertEqual(validate_model_root(value), Path('/tmp/My SSD/models'))
            result = subprocess.run(['bash', '-c', '. "$1"; validate_model_root_shell "$2" && printf "%s" "$VALIDATED_MODEL_ROOT"',
                                     'bash', str(ROOT / 'scripts/path_validation.sh'), value],
                                    capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '/tmp/My SSD/models')

    def test_empty_and_root_remain_rejected(self):
        for value in ('', ' \t\u00a0', '/', ' / \u00a0', '//', '/./'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_model_root(value)
            result = subprocess.run(['bash', '-c', '. "$1"; validate_model_root_shell "$2"',
                                     'bash', str(ROOT / 'scripts/path_validation.sh'), value], capture_output=True)
            self.assertNotEqual(result.returncode, 0, value)

    def test_literal_backslash_is_not_unescaped(self):
        self.assertEqual(str(validate_model_root('/tmp/models\\ ')), '/tmp/models\\')

    def test_internal_spaces_preserved(self):
        self.assertEqual(validate_model_root('/Volumes/My SSD/AI models'), Path('/Volumes/My SSD/AI models'))

    def test_no_directory_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            old = Path(folder) / 'models '
            old.mkdir()
            (old / 'sentinel').write_text('保留')
            self.assertEqual(validate_model_root(old), Path(folder) / 'models')
            self.assertEqual((old / 'sentinel').read_text(), '保留')
            self.assertFalse((Path(folder) / 'models').exists())

    def test_shell_matches_python_on_whitespace(self):
        # 默认 locale 下核对 Unicode 与 ASCII；不运行安装或下载脚本。
        for value, valid in [('/tmp/中文目录/models', True), ('/tmp/My SSD/models', True), ('/tmp/models ', True),
                             ('/tmp/a /models', False), ('/tmp/\ta/models', False),
                             ('/tmp/\u3000a/models', False), ('/tmp/models\u00a0', True)]:
            result = subprocess.run(['bash', '-c', '. "$1"; validate_model_root_shell "$2"',
                                     'bash', str(ROOT / 'scripts/path_validation.sh'), value],
                                    text=True, capture_output=True, env={**os.environ, 'LC_ALL': 'C'})
            self.assertEqual(result.returncode == 0, valid, (value, result.stderr))
            if not valid:
                self.assertIn('显式重新选择', result.stderr)

    def test_downloader_cli_normalizes_actual_plan(self):
        import contextlib
        import io
        import json
        import download_models
        for flag in ('--models-dir', '--dest'):
            with tempfile.TemporaryDirectory() as folder:
                base = Path(folder) / 'My models'
                base.mkdir()
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(download_models.main([flag, ' \t' + str(base) + '\u00a0',
                                                          '--repo', 'owner/model', '--dry-run', '--details']), 0)
                text = output.getvalue()
                payload = json.loads(text[text.index('{'):])
                self.assertEqual(payload['models_dir'], str(base))
                self.assertFalse((base / 'models').exists())
                self.assertFalse(Path(str(base) + '\u00a0').exists())

    def test_empty_cli_all_python_entrypoints_fail(self):
        for script in ('download_models.py', 'model_inventory.py', 'merge_h3_models.py', 'llama_benchmark.py'):
            for value in ('', ' \t\u00a0'):
                result = subprocess.run([sys.executable, str(ROOT / 'scripts' / script), '--models-dir', value],
                                        capture_output=True, text=True, stdin=subprocess.DEVNULL)
                self.assertNotEqual(result.returncode, 0, (script, value))
                self.assertIn('模型目录无效', result.stderr)

    def test_cli_rejects_before_dependencies_or_creating_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = str(Path(folder) / 'models ' / 'child')
            commands = [
                ['bash', str(ROOT / 'start.sh'), 'download', '--models-dir', bad],
                ['bash', str(ROOT / 'scripts/setup_ai_capabilities.sh'), '--models-dir', bad],
                [sys.executable, str(ROOT / 'scripts/model_inventory.py'), '--models-dir', bad],
                [sys.executable, str(ROOT / 'scripts/merge_h3_models.py'), '--models-dir', bad],
                [sys.executable, str(ROOT / 'scripts/llama_benchmark.py'), '--models-dir', bad],
            ]
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(command, text=True, capture_output=True, stdin=subprocess.DEVNULL)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('首尾含空白', result.stderr)
                    self.assertFalse(Path(bad).exists())


if __name__ == '__main__':
    unittest.main()
