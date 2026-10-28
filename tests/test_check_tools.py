import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_tools.py'
spec = importlib.util.spec_from_file_location('check_tools', SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class CheckToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / 'AI'
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'HERMES_HOME': str(self.home / '.hermes')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.which = patch.object(check.shutil, 'which', return_value=None)
        self.which.start()
        self.addCleanup(self.which.stop)

    def create(self, relative, executable=False):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('SECRET_TOKEN=do-not-display\n')
        if executable:
            path.chmod(0o755)
        return path

    def test_missing_root_is_readonly(self):
        rows = check.inventory(self.root, self.home, [])
        self.assertEqual(len(rows), 7)
        self.assertFalse(self.root.exists())
        self.assertIn('未发现', rows[1]['installed'])
        self.assertEqual('未发现', rows[1]['configured'])

    def test_metadata_does_not_execute_binary_or_read_config(self):
        self.create('bin/llama-server', True)
        self.create('config/llama-server.json')
        with patch.object(Path, 'open', side_effect=AssertionError('must not read files')), patch.object(check.subprocess, 'run', side_effect=AssertionError('must not execute components')):
            row = check.inventory(self.root, self.home, [(321, 'llama-server')])[1]
        self.assertIn('已发现', row['installed'])
        self.assertIn('已发现', row['configured'])
        self.assertIn('PID 321', row['process'])
        self.assertNotIn('健康', row['process'])

    def test_exact_process_names_only(self):
        processes = check.parse_processes(' 20 /usr/bin/node\n21 /x/not-llama-server\n22 /x/llama-server-backup\n23 /x/MLX Core\n24 /x/llama-server\n25 python3\nbad text\n')
        rows = check.inventory(self.root, self.home, processes)
        self.assertIn('PID 24', rows[1]['process'])
        self.assertNotIn('21', rows[1]['process'])
        self.assertNotIn('22', rows[1]['process'])
        self.assertIn('PID 23', rows[0]['process'])
        self.assertNotIn('PID', rows[-1]['process'])
        self.assertIn('未核实', rows[-1]['process'])
        self.assertNotIn('PID', rows[-2]['process'])

    def test_process_command_excludes_args_and_env(self):
        result = subprocess.CompletedProcess([], 0, '12 /x/sd-server\n', '')
        with patch.object(check.subprocess, 'run', return_value=result) as run:
            self.assertEqual(check.read_processes(), [(12, 'sd-server')])
        self.assertIn(run.call_args.args[0][0], ('/bin/ps', '/usr/bin/ps'))
        self.assertEqual(run.call_args.args[0][1:], ['-axo', 'pid=,comm='])
        self.assertEqual(run.call_args.kwargs['timeout'], 5)

    def test_process_failure_is_unknown_not_stopped(self):
        with patch.object(check.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ps', 5)):
            self.assertIsNone(check.read_processes())
        for row in check.inventory(self.root, self.home, None):
            self.assertIn('未核实', row['process'])

    def test_symlink_config_not_followed(self):
        outside = self.home / 'outside'
        outside.mkdir()
        (outside / 'llama-swap.yaml').write_text('secret')
        self.root.mkdir()
        (self.root / 'config').symlink_to(outside, target_is_directory=True)
        self.assertEqual(check.metadata(self.root / 'config/llama-swap.yaml'), 'unknown')
        row = check.inventory(self.root, self.home, [])[2]
        self.assertIn('未核实', row['configured'])

    def test_symlink_and_nonexecutable_binary_are_unverified(self):
        binary = self.create('bin/llama-server')
        self.assertEqual(check.metadata(binary, executable=True), 'unknown')
        binary.unlink()
        binary.symlink_to(self.home / 'missing')
        self.assertEqual(check.metadata(binary, executable=True), 'unknown')

    def test_actual_remotion_config_metadata(self):
        base = self.home / 'Library/Application Support/RemotionRemote'
        project = base / ('a' * 16)
        project.mkdir(parents=True)
        (project / 'config.json').write_text('SECRET')
        rows = check.inventory(self.root, self.home, [(99, 'node')])
        self.assertEqual(rows[-1]['configured'], '已发现')
        self.assertEqual(rows[-1]['process'], '未核实')

    def test_remotion_symlink_and_scan_limit(self):
        base = self.home / 'Library/Application Support/RemotionRemote'
        base.mkdir(parents=True)
        external = self.home / 'external'
        external.mkdir()
        (external / 'config.json').write_text('SECRET')
        (base / ('b' * 16)).symlink_to(external, target_is_directory=True)
        self.assertEqual(check.remotion_configs(self.home)[0][1], 'unknown')
        for index in range(130):
            (base / ('item' + str(index))).mkdir()
        self.assertIn((base, 'unknown'), check.remotion_configs(self.home))

    def test_details_preserve_chinese(self):
        self.create('config/audio-server.json')
        with patch.object(check, 'read_processes', return_value=[]), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(check.main(['--details']), 0)
        self.assertNotIn('\\u', out.getvalue())

    def test_cli_help(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as raised:
                check.main(['--help'])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn('--root', out.getvalue())

    def test_bad_paths_report_error(self):
        for value in ('/', 'relative', '/tmp/../bad', '/tmp/evil\nvalue'):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(check.main(['--root', value]), 2)
        with patch.dict(os.environ, {'HERMES_HOME': 'bad'}), patch.object(check, 'read_processes', return_value=[]), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(check.main([]), 2)

    def test_details_never_print_config_contents(self):
        self.create('config/audio-server.json')
        with patch.object(check, 'read_processes', return_value=[]), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(check.main(['--details']), 0)
        self.assertNotIn('SECRET_TOKEN', out.getvalue())
        self.assertIn('audio-server.json', out.getvalue())
        self.assertIn('未启动或停止工具', out.getvalue())


if __name__ == '__main__':
    unittest.main()
