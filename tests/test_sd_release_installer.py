"""SD.cpp 安装入口离线回归；发行解析与发布安全见 helper 测试。"""

from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/setup_ai_capabilities.sh'


class SdReleaseInstallerTests(unittest.TestCase):
    def test_preview_is_offline_and_stable_directory(self):
        r = subprocess.run(
            ['bash', str(SCRIPT), '--select', 'sd.cpp', '--root', '/tmp/example-ai-root', '--dry-run'],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('/tmp/example-ai-root/apps/sd.cpp（官方最新发行版）', r.stdout)
        self.assertNotIn('master-1006', r.stdout)
        self.assertNotIn('固定版', r.stdout)

    def test_installer_resolves_one_plan_then_installs_it(self):
        source = SCRIPT.read_text()
        self.assertEqual(source.count('sd_release.py" resolve'), 1)
        self.assertEqual(source.count('sd_release.py" install'), 1)
        self.assertIn('--plan "$TEMP/sd-release.json"', source)
        self.assertNotIn('prepare_sd_install', source)
        self.assertNotIn('sd.cpp-master-', source)

    def test_dynamic_install_does_not_run_brew_or_build(self):
        source = SCRIPT.read_text()
        part = source[
            source.index('  # 本次只解析一次 latest') : source.index(
                ' elif command -v "$CMD"', source.index('  # 本次只解析一次 latest')
            )
        ]
        self.assertNotIn('brew ', part)
        self.assertNotIn('cmake ', part)
        self.assertIn('continue', part)


if __name__ == '__main__':
    unittest.main()
