"""默认安装预览保持简洁；详细模式保留核验信息，预览不改文件。"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ConciseInstallerOutputTests(unittest.TestCase):
    def test_capability_preview_keeps_targets_and_consequences(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, HOME=td)
            script = ROOT / 'scripts/setup_ai_capabilities.sh'
            normal = subprocess.run(['bash', str(script), '--dry-run', '--select', 'all'], env=env, text=True, capture_output=True)
            detailed = subprocess.run(['bash', str(script), '--dry-run', '--select', 'all', '--details'], env=env, text=True, capture_output=True)
            self.assertEqual(normal.returncode, 0, normal.stderr)
            self.assertEqual(detailed.returncode, 0, detailed.stderr)
            for name in ('MLX-Serve', 'llama.cpp', 'sd.cpp', 'audio.cpp', 'Hermes'):
                self.assertIn(name, normal.stdout)
            for text in ('~/Applications/MLX-Serve.app', '~/.hermes', '~/.local/bin', 'Node/工具链', 'shell PATH 配置文件'):
                self.assertIn(text, normal.stdout)
            self.assertNotIn('SHA256', normal.stdout)
            self.assertNotIn('https://', normal.stdout)
            self.assertIn('SHA256', detailed.stdout)
            self.assertIn('https://github.com/', detailed.stdout)
            self.assertIn('--skip-browser --skip-computer-use', detailed.stdout)
            self.assertEqual(list(Path(td).iterdir()), [])

    def test_environment_preview_hides_versions_and_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); bins = root / 'mock bin'; bins.mkdir()
            home = root / 'home'; home.mkdir()
            for name in ('brew', 'macmon', 'uv', 'python3', 'fnm', 'node', 'npm', 'docker', 'ffmpeg'):
                p = bins / name
                p.write_text('#!/bin/sh\nprintf "ONLY_DETAIL_VERSION\\n"\n')
                p.chmod(0o755)
            env = dict(os.environ, HOME=str(home), PATH=str(bins)+':/usr/bin:/bin')
            script = ROOT / 'scripts/setup_mac_environment.sh'
            normal = subprocess.run(['bash', str(script), '--dry-run'], env=env, text=True, capture_output=True)
            detailed = subprocess.run(['bash', str(script), '--dry-run', '--details'], env=env, text=True, capture_output=True)
            self.assertEqual(normal.returncode, 0, normal.stderr)
            self.assertEqual(detailed.returncode, 0, detailed.stderr)
            self.assertIn('[uv] 已找到', normal.stdout)
            self.assertIn('安装运行工具 → 准备基础工具', normal.stdout)
            self.assertNotIn('bash scripts/', normal.stdout)
            self.assertNotIn('ONLY_DETAIL_VERSION', normal.stdout)
            self.assertNotIn(str(bins), normal.stdout)
            self.assertIn('ONLY_DETAIL_VERSION', detailed.stdout)
            self.assertIn(str(bins), detailed.stdout)
            self.assertEqual(list(home.iterdir()), [])

    def test_finish_message_does_not_require_global_path(self):
        cap = (ROOT / 'scripts/setup_ai_capabilities.sh').read_text()
        self.assertNotIn('如有需要，请将此目录加入 PATH', cap)
        self.assertIn('这里只放启动脚本', cap)
        self.assertIn('菜单内无需设置 PATH', cap)
        self.assertIn('回车取消', cap)
        base = (ROOT / 'scripts/setup_mac_environment.sh').read_text()
        self.assertIn('y 执行，回车跳过', base)

    def test_sensitive_setup_disclosures_are_not_hidden(self):
        text = (ROOT / 'scripts/setup_mac_environment.sh').read_text()
        for text_fragment in ('管理员密码', 'https://www.docker.com/legal/docker-subscription-service-agreement/', '配置：仅向 ~/.zprofile 追加缺少的 Homebrew 初始化'):
            self.assertIn(text_fragment, text)
        cap = (ROOT / 'scripts/setup_ai_capabilities.sh').read_text()
        self.assertIn('安装以上组件及已说明的依赖 / 设置更改？[y/N]', cap)

if __name__ == '__main__':
    unittest.main()
