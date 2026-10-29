"""Offline persistence/dispatch tests: no models, services or installers run."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PersistentDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.env = {**os.environ, 'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / '.config')}
        self.scripts = ROOT / 'scripts'

    def shell(self, code, *args):
        prefix = '. "$1/path_validation.sh"; . "$1/model_directory.sh"; shift; '
        return subprocess.run(
            ['bash', '-c', prefix + code, 'bash', str(self.scripts), *args],
            env=self.env,
            text=True,
            capture_output=True,
        )

    def save(self, path):
        result = self.shell('save_model_directory "$1"', path)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_persist_restart_trim_and_preserve_interior_spaces(self):
        path = str(self.home / 'My SSD/AI models')
        self.save(' \t' + path + '\u3000')
        result = self.shell('load_model_directory && printf "%s|%s" "$MODEL_DIR" "$DIRECTORY_CHOSEN"')
        self.assertEqual(result.stdout, path + '|1')
        self.assertFalse(Path(path).exists())
        config = self.home / '.config/mac-ai-kit/model-directory'
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_no_saved_setting_has_old_default_without_writing(self):
        result = self.shell('load_model_directory && printf "%s|%s" "$MODEL_DIR" "$DIRECTORY_CHOSEN"')
        self.assertEqual(result.stdout, str(self.home / 'AI/models') + '|0')
        self.assertFalse((self.home / '.config').exists())

    def test_config_is_data_not_shell_code(self):
        path = str(self.home / '$(touch PWNED)')
        self.save(path)
        result = self.shell('load_model_directory && printf "%s" "$MODEL_DIR"')
        self.assertEqual(result.stdout, path)
        self.assertFalse((self.home / 'PWNED').exists())

    def test_missing_external_volume_retains_setting_and_blocks(self):
        path = '/Volumes/MacAIKitOfflineTestMissingDisk/AI/models'
        self.save(path)
        result = self.shell('load_model_directory && model_volume_ready "$MODEL_DIR"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('外置磁盘未挂载', result.stderr)
        self.assertIn('不会改用默认目录', result.stderr)
        self.assertEqual(self.shell('load_model_directory; printf "%s" "$MODEL_DIR"').stdout, path)

    def test_invalid_saved_setting_never_silently_falls_back(self):
        config = self.home / '.config/mac-ai-kit/model-directory'
        config.parent.mkdir(parents=True)
        config.write_text('relative/path\n')
        result = self.shell('load_model_directory')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('不会退回默认目录', result.stderr)

    def test_entry_dispatches_saved_root_to_all_model_tasks(self):
        # Tiny substitute scripts only print argv; no production task executes.
        kit = self.base / 'kit'
        scripts = kit / 'scripts'
        scripts.mkdir(parents=True)
        shutil.copy2(ROOT / 'start.sh', kit / 'start.sh')
        for name in ('path_validation.sh', 'model_directory.sh'):
            shutil.copy2(self.scripts / name, scripts / name)
        for name in (
            'api_inventory.py',
            'download_models.py',
            'model_inventory.py',
            'llama_benchmark.py',
            'merge_h3_models.py',
            'check_tools.py',
        ):
            (scripts / name).write_text('import sys; print(repr(sys.argv[1:]))\n')
        path = str(self.home / 'My SSD/AI/models')
        self.save(path)
        for task in ('api', 'models', 'installed-models', 'benchmark', 'merge-h3', 'check-tools'):
            with self.subTest(task=task):
                result = subprocess.run(
                    ['bash', str(kit / 'start.sh'), task], env=self.env, text=True, capture_output=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(path, result.stdout)
        result = subprocess.run(
            ['bash', str(kit / 'start.sh'), 'download', '--dry-run'], env=self.env, text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(path, result.stdout)

    def test_real_menu_save_and_api_after_restart(self):
        path = self.home / 'My SSD/AI/models'
        path.mkdir(parents=True)
        result = subprocess.run(
            ['bash', str(ROOT / 'start.sh')],
            input='d\n  ' + str(path) + '  \nq\n',
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(
            ['bash', str(ROOT / 'start.sh')], input='a\nq\n', env=self.env, text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((path / 'API.md').is_file())
        self.assertIn('检查目录：' + str(path), (path / 'API.md').read_text())
        self.assertNotIn('检查目录 [', result.stdout)
        self.assertNotIn('模型目录 [', result.stdout)
        self.assertFalse((self.home / 'AI').exists())

    def test_entry_api_does_not_write_default_on_absent_external_volume(self):
        self.save('/Volumes/MacAIKitOfflineTestMissingDisk/AI/models')
        result = subprocess.run(['bash', str(ROOT / 'start.sh'), 'api'], env=self.env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('外置磁盘未挂载', result.stderr)
        self.assertFalse((self.home / 'AI').exists())


if __name__ == '__main__':
    unittest.main()
