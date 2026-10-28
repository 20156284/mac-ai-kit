"""No downloads: mocked interactive base-tool plan and startup boundaries."""
import os
from pathlib import Path
import pty
import select
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/setup_mac_environment.sh'

class SinglePlanTests(unittest.TestCase):
    def run_plan(self, answer, locale='C'):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); home = base/'home'; home.mkdir(); bins = base/'bin'; bins.mkdir()
            mocks = {
                'uname': 'case "$1" in -s) echo Darwin;; -m) echo arm64;; esac',
                'id': 'echo 501',
                'brew': 'case "$1" in --prefix) echo /opt/homebrew;; list) exit 1;; *) exit 98;; esac',
                'xcode-select': 'exit 0', 'xcrun': 'exit 0',
                'python3': 'exit 0',
            }
            for name in ('macmon', 'ffmpeg', 'uv', 'fnm', 'node', 'npm'):
                mocks[name] = 'exit 0'
            for name, body in mocks.items():
                path = bins/name; path.write_text('#!/bin/sh\n'+body+'\n'); path.chmod(0o755)
            master, slave = pty.openpty()
            env = dict(os.environ, HOME=str(home), PATH=str(bins)+':/usr/bin:/bin', LC_ALL=locale, NVM_DIR=str(home/'nvm'))
            child = subprocess.Popen(['bash', str(SCRIPT), '--install'], stdin=slave, stdout=slave, stderr=slave, env=env)
            os.close(slave); os.write(master, answer.encode()); chunks=[]; deadline=time.monotonic()+10
            try:
                while time.monotonic()<deadline:
                    if select.select([master], [], [], .1)[0]:
                        try: data=os.read(master, 65536)
                        except OSError: break
                        if not data: break
                        chunks.append(data)
                    elif child.poll() is not None: break
                child.wait(timeout=2)
            finally:
                if child.poll() is None: child.kill(); child.wait()
                os.close(master)
            return child.returncode, b''.join(chunks).decode(), {p.name: p.read_text() for p in home.iterdir() if p.is_file()}

    def test_one_base_confirmation_separate_optional_docker(self):
        code, text, files = self.run_plan('y\nn\n')
        self.assertEqual(code, 0, text)
        docker_present = Path('/Applications/Docker.app').is_dir()
        self.assertEqual(text.count('[y/N]'), 1 if docker_present else 2, text)
        for name in ('macmon', 'ffmpeg', 'uv', 'fnm', 'node'):
            self.assertIn(name, text)
        self.assertIn('主菜单 6「服务管理」', text)
        self.assertIn('没有创建任何登录启动项', text)
        if docker_present:
            self.assertIn('Docker Desktop 应用已存在', text)
        else:
            self.assertIn('不接受许可', text)
        self.assertIn('brew shellenv', files['.zprofile'])
        self.assertIn('fnm env', files['.zshrc'])
        self.assertNotIn('??', text)

    def test_declining_plan_writes_nothing(self):
        code, text, files = self.run_plan('n\n')
        self.assertEqual(code, 0, text)
        self.assertEqual(text.count('[y/N]'), 1)
        self.assertEqual(files, {})

    def test_missing_formula_installs_without_extra_confirmation(self):
        text = SCRIPT.read_text()
        function = text[text.index('install_formula() {'):text.index('for formula in macmon ffmpeg uv;')]
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); brew = base/'mock-brew'; tool = base/'macmon'
            brew.write_text('#!/bin/sh\n[ "$1" != list ] || exit 1\nprintf "#!/bin/sh\\nexit 0\\n" > "$MOCK_BIN/$2"\nchmod +x "$MOCK_BIN/$2"\n')
            brew.chmod(0o755)
            harness = 'say() { printf "%s\\n" "$*"; }; fail() { exit 91; }; has() { command -v "$1" >/dev/null; }; confirm() { exit 92; };\n'+function+'\ninstall_formula macmon\n'
            env = dict(os.environ, BREW=str(brew), MOCK_BIN=td, PATH=td+':/usr/bin:/bin', LC_ALL='C')
            result = subprocess.run(['bash', '-c', harness], env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertTrue(tool.exists())
            self.assertIn('[安装] macmon (brew install macmon，含必要依赖)', result.stdout)

    def test_install_formula_uses_format_arguments_not_unicode_adjacent_variable(self):
        text = SCRIPT.read_text()
        self.assertIn("printf '[安装] %s (brew install %s，含必要依赖)\\n'", text)
        self.assertNotIn('if confirm "安装 $name', text)
        self.assertNotIn('NONINTERACTIVE=', text)
        self.assertNotIn('export LC_ALL=', text)
        cap = (ROOT/'scripts/setup_ai_capabilities.sh').read_text()
        self.assertIn('配置和运行状态尚未核验', cap)

if __name__ == '__main__': unittest.main()
