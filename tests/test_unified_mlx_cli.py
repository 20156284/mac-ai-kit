"""桌面 App 安装流程：不创建 CLI、不运行现有 App，仅离线模拟。"""

import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/setup_ai_capabilities.sh'


class DesktopMlxTests(unittest.TestCase):
    def scenario(
        self,
        existing=False,
        broken=False,
        version='26.10.2',
        missing=False,
        sandbox=False,
        signature_error=False,
        receipt=False,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home with 'quote"
            home.mkdir()
            bins = base / 'mock'
            bins.mkdir()
            root = home / 'AI space'
            app = home / 'Applications/MLX-Serve.app'
            contents = app / 'Contents'
            (contents / 'MacOS').mkdir(parents=True)
            fw = contents / 'Frameworks'
            fw.mkdir()
            (contents / 'Info.plist').write_bytes(
                plistlib.dumps({'CFBundleIdentifier': 'com.dalcu.mlx-core', 'CFBundleShortVersionString': version})
            )
            for name in ['MLXCore', 'mlx-serve']:
                p = contents / 'MacOS' / name
                p.write_text('#!/bin/sh\nprintf "%s\\n" "$MLX_METAL_PATH" "$DYLD_LIBRARY_PATH" "$@"\n')
                p.chmod(0o755)
            for name in ['libmlxc.dylib', 'libmlx.dylib', 'libwebp.dylib', 'libllama.dylib', 'mlx.metallib']:
                if not missing or name != 'mlx.metallib':
                    (fw / name).touch()
            if receipt:
                (contents / '_MASReceipt').mkdir()
            log = base / 'calls'
            local = home / '.local/bin/mlx-serve'
            if existing or broken:
                local.parent.mkdir(parents=True)
                if broken:
                    local.symlink_to('/missing/not-a-real-cli')
                else:
                    local.write_text('#!/bin/sh\nexit 0\n')
                    local.chmod(0o755)
            commands = {
                'uname': '[ "$1" = -s ] && echo Darwin || echo arm64',
                'id': 'echo 501',
                'sw_vers': 'echo 26.2',
                'curl': 'echo download >> "$MOCK_LOG"; exit 87',
            }
            for name in ['hdiutil', 'ditto', 'defaults', 'pgrep']:
                commands[name] = 'exit 89'
            commands['codesign'] = "echo '<plist><dict/></plist>'"
            if sandbox:
                commands['codesign'] = (
                    "echo '<plist><dict><key>com.apple.security.app-sandbox</key><true/></dict></plist>'"
                )
            if signature_error:
                commands['codesign'] = 'exit 1'
            for name, body in commands.items():
                p = bins / name
                p.write_text('#!/bin/sh\n' + body + '\n')
                p.chmod(0o755)
            env = dict(os.environ, HOME=str(home), PATH=str(bins) + ':' + os.environ['PATH'], MOCK_LOG=str(log))
            proc = subprocess.run(
                ['bash', str(SCRIPT), '--apply', '--yes', '--select', 'mlx-serve', '--root', str(root)],
                env=env,
                text=True,
                capture_output=True,
            )
            wrapper = root / 'bin/mlx-serve'
            self.assertTrue(app.exists())
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertFalse(log.exists())
            self.assertFalse(wrapper.exists())
            self.assertFalse(wrapper.is_symlink())
            self.assertFalse((root / "bin").exists())
            self.assertFalse((root / "apps").exists())
            self.assertIn('请手动打开桌面 App', proc.stdout)
            if broken:
                self.assertTrue(local.is_symlink())
            if existing:
                self.assertEqual(local.read_text(), '#!/bin/sh\nexit 0\n')

    def test_default_keeps_app_without_cli_wrapper(self):
        self.scenario()

    def test_local_bin_outside_path_preserved(self):
        self.scenario(existing=True)

    def test_broken_legacy_cli_is_not_touched(self):
        self.scenario(broken=True)

    def test_unknown_app_version_is_kept_without_cli_probe(self):
        self.scenario(version='26.10.3')

    def test_missing_cli_resource_does_not_block_app(self):
        self.scenario(missing=True)

    def test_sandbox_app_is_kept_without_cli_probe(self):
        self.scenario(sandbox=True)

    def test_app_kept_without_cli_signature_probe(self):
        self.scenario(signature_error=True)

    def test_store_app_is_kept_without_cli_probe(self):
        self.scenario(receipt=True)

    def test_existing_cli_kept_with_unknown_app(self):
        self.scenario(existing=True, version='26.10.3')


if __name__ == '__main__':
    unittest.main()
