"""仅执行离线契约测试；不执行下载的代码或安装软件。"""

import os
from pathlib import Path
import subprocess
import tempfile
import tarfile
import io
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'setup_ai_capabilities.sh'


class CapabilityInstallerTests(unittest.TestCase):
    def run_script(self, *args, home=None):
        env = os.environ.copy()
        if home is not None:
            env['HOME'] = str(home)
        return subprocess.run(['bash', str(SCRIPT), *args], env=env, text=True, capture_output=True)

    def test_archive_rejects_symlink_dotdot_traversal(self):
        # 仅用本地构造的归档测试本工具包的解压片段。
        source = SCRIPT.read_text()
        marker = '"$PYTHON" - "$archive" "$stage" <<\'PY\'\n'
        extractor = source.split(marker, 1)[1].split('\nPY\n', 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'bad.tar.gz'
            stage = root / 'unpack'
            stage.mkdir()
            with tarfile.open(archive, 'w:gz') as tf:
                link = tarfile.TarInfo('a')
                link.type = tarfile.SYMTYPE
                link.linkname = '.'
                tf.addfile(link)
                payload = tarfile.TarInfo('a/../escape')
                payload.size = 4
                tf.addfile(payload, io.BytesIO(b'evil'))
            result = subprocess.run(
                ['python3', '-', str(archive), str(stage)], input=extractor, text=True, capture_output=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / 'escape').exists())
            self.assertEqual(list(stage.iterdir()), [])

    def test_app_bundle_is_the_only_install_source(self):
        result = self.run_script('--select', 'mlx-serve', '--details')
        self.assertEqual(result.returncode, 0)
        self.assertIn('/MLX-Serve.dmg', result.stdout)
        self.assertIn('MLX-Serve 桌面 App', result.stdout)
        self.assertNotIn('内置 CLI', result.stdout)
        self.assertNotIn('mlx-serve-bin-macos-arm64.tar.gz', SCRIPT.read_text())
        self.assertNotIn('MLX_MODE', SCRIPT.read_text())

    def test_optional_swap_is_never_selected_by_all(self):
        r = self.run_script('--choose-only', '--select', 'all')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('llama-swap', r.stdout)
        r = self.run_script('--choose-only', '--select', '3')
        self.assertEqual(r.stdout.strip(), 'llama-swap')

    def test_cli_plumbing_removed(self):
        source = SCRIPT.read_text()
        for obsolete in (
            'existing_mlx_cli',
            'reuse_bundled_cli',
            'DYLD_LIBRARY_PATH',
            'MLX_METAL_PATH',
            '$ROOT/bin/mlx-serve',
        ):
            self.assertNotIn(obsolete, source)

    def test_removed_mode_option_is_rejected(self):
        for mode in ('gui', 'cli'):
            with self.subTest(mode=mode):
                result = self.run_script('--select', 'mlx-serve', '--mlx-mode', mode)
                self.assertEqual(result.returncode, 2)
                self.assertIn('未知选项', result.stderr)
        self.assertNotIn('--mlx-mode', self.run_script('--help').stdout)

    def test_external_shared_path_preview_no_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'External SSD' / 'models'
            result = self.run_script('--select', 'mlx-serve', '--models-dir', str(path), '--details')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(path), result.stdout)
            self.assertIn('defaultModelDownloadPath', result.stdout)
            self.assertIn('backups', result.stdout)
            self.assertFalse(path.exists())

    def test_gui_model_arguments_rejected_when_invalid(self):
        for args in [
            ('--mlx-mode', 'unknown'),
            ('--models-dir', '/'),
            ('--models-dir', 'relative'),
            ('--models-dir',),
            ('--yes',),
        ]:
            with self.subTest(args=args):
                self.assertEqual(self.run_script(*args).returncode, 2)

    def test_shell_syntax(self):
        self.assertEqual(subprocess.run(['bash', '-n', str(SCRIPT)]).returncode, 0)

    def test_default_preview_writes_nothing_and_hermes_is_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'
            home.mkdir()
            r = self.run_script('--details', home=home)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(list(home.iterdir()), [])
            self.assertIn('仅预览', r.stdout)
            self.assertLess(r.stdout.index('audio.cpp v'), r.stdout.index('hermes v'))

    def test_selection_order_does_not_move_hermes_first(self):
        r = self.run_script('--select', 'hermes,llama.cpp', '--details')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(r.stdout.index('llama.cpp b'), r.stdout.index('hermes v'))
        self.assertNotIn('sd.cpp master', r.stdout)

    def test_invalid_input_rejected(self):
        for args in [('--select', 'unknown'), ('--root', '/'), ('--root', 'relative'), ('--select',)]:
            with self.subTest(args=args):
                self.assertEqual(self.run_script(*args).returncode, 2)

    def test_noninteractive_apply_needs_explicit_selection(self):
        r = self.run_script('--apply', '--yes')
        self.assertEqual(r.returncode, 2)
        self.assertIn('明确指定 --select', r.stderr)

    def test_non_mac_apply_cannot_mutate(self):
        if os.uname().sysname == 'Darwin':
            self.skipTest('主机平台保护测试仅适用于 Linux')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'must-not-exist'
            r = self.run_script('--apply', '--yes', '--select', 'all', '--root', str(root))
            self.assertEqual(r.returncode, 2)
            self.assertFalse(root.exists())
            self.assertIn('需要原生 arm64 macOS', r.stderr)

    def test_pinned_hermes_preview_discloses_dependencies(self):
        r = self.run_script('--select', 'hermes', '--details')
        self.assertIn('d923a5eb487a17c0a68801bba9ef0f56c81040a8', r.stdout)
        self.assertIn('--skip-browser', r.stdout)
        self.assertIn('--skip-computer-use', r.stdout)
        self.assertIn('Node/工具链', r.stdout)
        self.assertIn('shell PATH 配置文件', r.stdout)


if __name__ == '__main__':
    unittest.main()
