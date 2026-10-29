"""报告归档目录回归；不请求模型接口。"""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import llama_benchmark as llama
import mlx_benchmark as mlx


class ReportPathsTests(unittest.TestCase):
    def test_defaults_are_project_relative_not_cwd(self):
        old = Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                self.assertEqual(llama.default_report_root(), ROOT / 'output/reports')
                first, second = mlx.default_report_directory(), mlx.default_report_directory()
                self.assertEqual(first.parent, ROOT / 'output/reports')
                self.assertTrue(first.name.startswith('mlx-benchmark-'))
                self.assertNotEqual(first, second)
                self.assertEqual(list(Path(folder).iterdir()), [])
            finally:
                os.chdir(old)

    def test_mlx_explicit_output_wins(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(mlx, 'choose_model', return_value='mock'),
            patch.object(mlx, 'run_benchmark', return_value={'results': []}) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(mlx.main(['--base-url', 'http://localhost:11234/v1', '--output', folder]), 0)
            self.assertEqual(run.call_args.args[2], Path(folder))

    def test_llama_explicit_output_wins(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(llama.sys, 'argv', ['benchmark', '--base-url', llama.DEFAULT_URL, '--output', folder]),
            patch.object(llama.shutil, 'which', return_value='/bin/uvx'),
            patch.object(llama, 'discover', return_value=[{'id': 'org/model'}]),
            patch.object(llama, 'resolve_tokenizer', return_value={'kind': 'public', 'value': 'org/model'}),
            patch('builtins.input', side_effect=['']),
            patch.object(llama, 'run', return_value=0) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(llama.main(), 0)
            self.assertEqual(run.call_args.args[5], Path(folder))

    def test_gitignore_covers_all_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / '.gitignore').write_text((ROOT / '.gitignore').read_text())
            subprocess.run(['git', 'init', '-q', str(path)], check=True)
            for name in (
                'output/reports/mlx-benchmark-time/results.csv',
                'output/reports/llama-benchmark-time/stdout.log',
                'output/reports/mlx-benchmark-time/report.html',
            ):
                result = subprocess.run(['git', '-C', str(path), 'check-ignore', name], capture_output=True)
                self.assertEqual(result.returncode, 0, name)
