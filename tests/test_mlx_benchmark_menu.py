"""MLX entry routing uses a recording stub, never a service or inference."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MlxBenchmarkMenuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'scripts').mkdir()
        shutil.copy2(ROOT / 'start.sh', self.root / 'start.sh')
        for name in ('path_validation.sh', 'model_directory.sh', 'terminal_ui.sh'):
            shutil.copy2(ROOT / 'scripts' / name, self.root / 'scripts' / name)
        (self.root / 'scripts/mlx_benchmark.py').write_text(
            'import json, sys\nprint("MLX_ARGS=" + json.dumps(sys.argv[1:]))\n')
        self.env = dict(os.environ, HOME=str(self.root / 'home'),
                        XDG_CONFIG_HOME=str(self.root / 'config'), NO_COLOR='1')
        config = self.root / 'config/mac-ai-kit'
        config.mkdir(parents=True)
        self.saved = str(self.root / 'saved-models')
        (config / 'model-directory').write_text(self.saved + '\n')

    def run_start(self, *args, input=''):
        return subprocess.run(['bash', str(self.root / 'start.sh'), *args],
            input=input, text=True, capture_output=True, env=self.env, timeout=10)

    def test_advanced_routes_saved_directory_and_explicit_url(self):
        p = self.run_start('mlx-benchmark', '--base-url', 'http://127.0.0.1:8080/v1')
        self.assertEqual(p.returncode, 0, p.stderr)
        args = json.loads(p.stdout.split('MLX_ARGS=', 1)[1].splitlines()[0])
        self.assertEqual(args, ['--models-dir', self.saved, '--base-url', 'http://127.0.0.1:8080/v1'])
        self.assertFalse((self.root / 'saved-models').exists())

    def test_directory_override_does_not_rewrite_saved_choice(self):
        override = str(self.root / 'override-models')
        p = self.run_start('mlx-benchmark', '--models-dir', override, '--help')
        self.assertEqual(p.returncode, 0, p.stderr)
        args = json.loads(p.stdout.split('MLX_ARGS=', 1)[1].splitlines()[0])
        self.assertEqual(args, ['--models-dir', self.saved, '--models-dir', override, '--help'])
        self.assertEqual((self.root / 'config/mac-ai-kit/model-directory').read_text(), self.saved + '\n')

    def test_script_help_does_not_start_test(self):
        p = self.run_start('script-help', 'mlx-benchmark')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('"--help"', p.stdout)

    def test_second_benchmark_entry_and_return(self):
        p = self.run_start(input='5\n2\n\n0\n0\n')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('MLX_ARGS=', p.stdout)
        self.assertIn('MLX-Serve 长上下文 benchmark', p.stdout)
        self.assertIn('0、50K、100K、200K 四档测试', p.stdout)
        self.assertNotIn('PP 计时不准', p.stdout)
        self.assertIn('0、50K、100K、200K 上下文吞吐测试；PP 计时不兼容 MLX-Serve、oMLX 等框架的空 role 消息。', p.stdout)

    def test_help_lists_both_commands(self):
        p = self.run_start('--help')
        self.assertEqual(p.returncode, 0)
        self.assertIn('mlx-benchmark', p.stdout)
        self.assertNotIn('MLX_ARGS=', p.stdout)


if __name__ == '__main__':
    unittest.main()
