"""图形应用安装控制流程测试，仅使用本地模拟命令；不挂载、不下载。"""
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/setup_ai_capabilities.sh'

class GuiReviewTests(unittest.TestCase):
    def run_mock(self, running=1, receipt=False, incomplete=False, padding="", old_pref="/old/shared/models"):
        with tempfile.TemporaryDirectory(prefix='gui-review-') as td:
            root=Path(td); home=root/'home space'; home.mkdir(); bins=root/'bin'; bins.mkdir()
            app=home/'Applications/MLX-Serve.app'; (app/'Contents/MacOS').mkdir(parents=True)
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.dalcu.mlx-core','CFBundleShortVersionString':'26.10.2'}))
            if not incomplete:
                binary=app/'Contents/MacOS/MLXCore'; binary.write_text('#!/bin/sh\nexit 99\n'); binary.chmod(0o755)
            if receipt: (app/'Contents/_MASReceipt').mkdir()
            models=root/"models with 'quote"; models.mkdir()
            pref=root/'preference'; pref.write_text(old_pref+'\n'); log=root/'calls'
            commands={
                'uname':'[ "$1" = -s ] && echo Darwin || echo arm64',
                'id':'echo 501',
                'codesign':"echo '<plist version=\"1.0\"><dict/></plist>'",
                'pgrep':f'exit {running}',
                'defaults':'''printf '%s\\n' "$*" >> "$MOCK_LOG"
case "$1" in
 read) cat "$MOCK_PREF" ;;
 write) printf '%s\\n' "$5" > "$MOCK_PREF" ;;
 *) exit 88 ;;
esac''',
                # 已有命令行工具不得跳过图形应用的核验与配置。
                'mlx-serve':'exit 99',
            }
            for name in ('curl','hdiutil','ditto','open'):
                commands[name]='printf "FORBIDDEN %s\\n" "$0" >> "$MOCK_LOG"; exit 99'
            for name,body in commands.items():
                p=bins/name; p.write_text('#!/bin/sh\n'+body+'\n'); p.chmod(0o755)
            env=dict(os.environ,HOME=str(home),PATH=str(bins)+':'+os.environ['PATH'],MOCK_PREF=str(pref),MOCK_LOG=str(log))
            proc=subprocess.run(['bash',str(SCRIPT),'--apply','--yes','--select','mlx-serve','--models-dir',padding+str(models)+padding],env=env,capture_output=True,text=True)
            calls=log.read_text() if log.exists() else ''
            self.assertNotIn('FORBIDDEN',calls)
            if running != 1 or receipt or incomplete:
                self.assertNotEqual(proc.returncode,0,proc.stdout+proc.stderr)
                self.assertNotIn('write ',calls)
                self.assertEqual(pref.read_text(),'/old/shared/models\n')
            else:
                self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
                self.assertEqual(pref.read_text(),str(models)+'\n')
                backups=list((home/'AI/backups').glob('*'))
                self.assertEqual(len(backups),1)
                self.assertEqual(backups[0].read_text(), 'PRESENT\n' + old_pref + '\n')
                self.assertIn('已复用（版本未经核验）',proc.stdout)

    def test_reused_gui_with_cli_still_sets_official_folder_and_backs_up(self): self.run_mock()
    def test_trimmed_folder_saved_and_old_whitespace_preference_backed_up(self):
        self.run_mock(padding=' \t\u00a0', old_pref='/old/shared/models \u00a0')
    def test_running_gui_never_changes_preference(self): self.run_mock(running=0)
    def test_process_probe_error_fails_closed(self): self.run_mock(running=2)
    def test_app_store_never_changes_preference(self): self.run_mock(receipt=True)
    def test_incomplete_existing_gui_not_reused(self): self.run_mock(incomplete=True)

if __name__=='__main__': unittest.main()
