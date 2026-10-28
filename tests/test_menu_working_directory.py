"""菜单不继承被解压更新移除的旧工作目录。"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import service_manager


class MenuWorkingDirectoryTests(unittest.TestCase):
    def test_pause_supplies_project_cwd(self):
        with patch.object(service_manager.subprocess, 'run') as run:
            service_manager.pause_menu()
        self.assertEqual(run.call_args.kwargs['cwd'], str(ROOT))

    def test_pause_from_deleted_cwd_has_no_shell_init_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            gone = Path(directory) / 'gone'; gone.mkdir()
            code = ('import os,sys; sys.path.insert(0,sys.argv[1]); import service_manager; '
                    'os.chdir(sys.argv[2]); os.rmdir(sys.argv[2]); service_manager.pause_menu()')
            result = subprocess.run([sys.executable, '-c', code, str(ROOT / 'scripts'), str(gone)],
                                    cwd=directory, stdin=subprocess.DEVNULL, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('getcwd', result.stderr)
            self.assertNotIn('shell-init', result.stderr)

    def test_missing_toolkit_gives_actionable_error(self):
        with patch.object(service_manager.Path, 'is_file', return_value=False):
            with self.assertRaisesRegex(service_manager.ServiceError, '重新运行'):
                service_manager.menu_working_directory()


if __name__ == '__main__':
    unittest.main()
