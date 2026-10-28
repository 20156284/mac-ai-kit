"""离线安装器审查测试；不执行真实安装器或下载二进制程序。"""
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CAP = ROOT / 'scripts/setup_ai_capabilities.sh'
ENV = ROOT / 'scripts/setup_mac_environment.sh'

class InstallerTests(unittest.TestCase):
    def test_bash_syntax(self):
        for script in (CAP, ENV):
            subprocess.run(['bash', '-n', str(script)], check=True)

    def test_preview_offline_no_writes(self):
        for script in (CAP, ENV):
            with tempfile.TemporaryDirectory() as td:
                base = Path(td); home = base / "home with spaces and 'quote"; home.mkdir()
                mock = base / 'bin'; mock.mkdir()
                for tool in ('curl', 'brew', 'macmon', 'uv', 'fnm', 'node', 'npm', 'docker', 'ffmpeg'):
                    f = mock / tool
                    f.write_text('#!/bin/sh\n[ "$1" = --version ] || [ "$1" = -version ] || exit 99\nprintf "mock-version\\n"\n')
                    f.chmod(0o755)
                env = dict(os.environ, HOME=str(home), PATH=str(mock)+':/usr/bin:/bin', NVM_DIR=str(home/'nvm'))
                before = list(home.rglob('*'))
                p = subprocess.run(['bash', str(script), '--dry-run'], env=env, capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
                self.assertEqual(list(home.rglob('*')), before)

    def test_invalid_argument(self):
        for script in (CAP, ENV):
            p = subprocess.run(['bash', str(script), '--not-real'], capture_output=True)
            self.assertEqual(p.returncode, 2)

    def test_fnm_only_no_manager_selection(self):
        p = subprocess.run(['bash', str(ENV), '--node-manager', 'nvm'], capture_output=True)
        self.assertEqual(p.returncode, 2)
        script = ENV.read_text()
        self.assertNotIn('nvm-sh/nvm', script)
        self.assertNotIn('nvm install', script)
        self.assertNotIn('NVM_READY', script)
        self.assertIn('install_formula fnm', script)

    def test_official_zsh_initialization(self):
        script = ENV.read_text()
        self.assertIn('RC="$HOME/.zprofile"', script)
        self.assertIn('RC="$HOME/.zshrc"', script)
        self.assertIn('eval "$(/opt/homebrew/bin/brew shellenv)"', script)
        self.assertIn('eval "$(fnm env --shell zsh)"', script)
        self.assertNotIn('.bash_profile', script)

    def test_tar_symlink_parent_escape_rejected(self):
        # 仅使用本地构造数据执行嵌入式解压代码。
        text = CAP.read_text()
        start = text.index('import os, pathlib, stat, sys, tarfile, zipfile')
        code = text[start:text.index('\nPY', start)]
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); stage = base/'stage'; stage.mkdir()
            archive = base/'fixture.tar.gz'
            with tarfile.open(archive, 'w:gz') as t:
                link = tarfile.TarInfo('a'); link.type=tarfile.SYMTYPE; link.linkname='.'; t.addfile(link)
                data=b'outside'; f=tarfile.TarInfo('a/../escape'); f.size=len(data); t.addfile(f,io.BytesIO(data))
            p = subprocess.run(['python3','-c',code,str(archive),str(stage)],capture_output=True)
            self.assertFalse((base/'escape').exists(), '解压写入了暂存目录之外')
            self.assertNotEqual(p.returncode, 0, '错误地接受了不安全归档')

if __name__ == '__main__': unittest.main()
