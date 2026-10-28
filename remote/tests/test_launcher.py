"""Launcher smoke tests. Fake macOS/launchctl/git/pnpm; no network or real services."""
import base64
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.project = self.root / "project with ' spaces"
        self.project.mkdir()
        (self.project / 'package.json').write_text('{}')
        cli = self.project / 'node_modules/@remotion/cli'
        cli.mkdir(parents=True)
        (cli / 'package.json').write_text('{"version":"4.0.534","bin":{"remotion":"remotion-cli.js"}}')
        (cli / 'remotion-cli.js').write_text('')
        self.output = self.root / 'output disk'
        self.output.mkdir()
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        for name, body in {
            'uname': 'echo Darwin',
            'pnpm': 'echo "PNPM MUST NOT EXECUTE" >&2; exit 99',
            'launchctl': 'printf "%s\\n" "$*" >> "$HOME/launchctl-calls"; if [ \"${SERVICE_LOADED:-}\" = 1 ] && [ \"$1\" = print ]; then exit 0; fi; exit 1',
            'git': '''shift 2
case "$1" in
rev-parse) echo true;;
symbolic-ref) if [ "${DETACHED:-}" = 1 ]; then exit 1; fi; echo main;;
config) case "$3" in *.remote) echo origin;; *.merge) echo refs/heads/main;; esac;;
remote) echo https://example.invalid/repo.git;;
ls-remote) [ "${GIT_FAIL:-}" != 1 ] || exit 128; echo 1234567890 refs/heads/main;;
*) exit 99;;
esac''',
        }.items():
            f = bin_dir / name
            f.write_text('#!/bin/bash\n' + body + '\n')
            f.chmod(0o700)
        runtime = 'const fs=require("fs");const c=JSON.parse(fs.readFileSync(process.argv[3]));console.log("CONFIG_OK "+c.repo);'
        text = (ROOT / 'src/launcher.sh').read_text()
        text = text.replace('RUNTIME_PAYLOAD_PLACEHOLDER', base64.b64encode(runtime.encode()).decode())
        text = text.replace('GUARD_PAYLOAD_PLACEHOLDER', base64.b64encode(b'// test guard').decode())
        text = text.replace('ADAPTER_PAYLOAD_PLACEHOLDER', base64.b64encode(b'// test adapter').decode())
        self.launcher = self.root / 'launcher.sh'
        self.launcher.write_text(text)
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(bin_dir) + ':' + os.environ['PATH'])
        self.id = hashlib.sha256(str(self.project).encode()).hexdigest()[:16]
        self.state = self.home / 'Library/Application Support/RemotionRemote' / self.id
        self.plist = self.home / 'Library/LaunchAgents' / ('local.remotion.remote.' + self.id + '.plist')

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, action, data='', extra_env=None):
        env = dict(self.env, **(extra_env or {}))
        return subprocess.run(['bash', str(self.launcher), '--project', str(self.project), action], input=data, text=True, capture_output=True, env=env)

    def setup_input(self, confirm='YES', output=None, retention='', username='operator', password='correct-horse-battery', password2=None):
        return '\n'.join([str(output or self.output), retention, username, password, password if password2 is None else password2, confirm, ''])

    def test_short_usernames_and_six_character_password(self):
        for username in ['a', 'ab', '中']:
            p = self.run_script('--configure', self.setup_input(username=username, password='123456'))
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            c = json.loads((self.state / 'config.json').read_text())
            self.assertEqual(c['username'], username)

    def test_trim_project_argument_and_output_preserving_internal_spaces(self):
        p = subprocess.run(['bash', str(self.launcher), '--project', ' \t' + str(self.project) + '\t ', '--configure'],
                           input=self.setup_input(output=' \t' + str(self.output) + '\t ', retention=' 24 ', username=' a '),
                           text=True, capture_output=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        c = json.loads((self.state / 'config.json').read_text())
        self.assertEqual(c['repo'], str(self.project))
        self.assertEqual(c['outputDir'], str(self.output))
        self.assertEqual(c['username'], 'a')
        self.assertEqual(c['retentionHours'], 24)
        self.assertIn('http://Mac的私网地址:43871/_remote/', p.stdout)

    def test_toolkit_entry_preserves_input_and_only_configures(self):
        kit = self.root / 'kit'
        (kit / 'remote').mkdir(parents=True)
        (kit / 'scripts').mkdir()
        shutil.copy2(ROOT.parent / 'start.sh', kit / 'start.sh')
        for name in ['terminal_ui.sh', 'path_validation.sh', 'model_directory.sh', 'path_validation.py']:
            shutil.copy2(ROOT.parent / 'scripts' / name, kit / 'scripts' / name)
        shutil.copy2(self.launcher, kit / 'remote/start-remotion-remote.sh')
        p = subprocess.run(['bash', str(kit / 'start.sh'), 'remote', '--project', '  ' + str(self.project) + '  '],
                           input=self.setup_input(output='  ' + str(self.output) + '  ', username='a', password='123456'),
                           text=True, capture_output=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn('CONFIG_OK', p.stdout)
        c = json.loads((self.state / 'config.json').read_text())
        self.assertEqual(c['repo'], str(self.project))
        self.assertEqual(c['outputDir'], str(self.output))
        self.assertEqual(c['username'], 'a')
        self.assertFalse(self.plist.exists())
        self.assert_no_service_mutations()

    def test_interactive_project_trim(self):
        p = subprocess.run(['bash', str(self.launcher), '--configure'],
                           input=' \t' + str(self.project) + ' \n' + self.setup_input(),
                           text=True, capture_output=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue((self.state / 'config.json').is_file())

    def test_url_not_mistaken_for_directory_or_fetched(self):
        for value in [' https://example.invalid/repo.git ', ' file:///tmp/project ', 'git@example.invalid:repo']:
            p = subprocess.run(['bash', str(self.launcher), '--project', value, '--configure'],
                               text=True, capture_output=True, env=self.env)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn('不接受 URL', p.stderr)
            self.assertFalse(self.state.exists())
        p = self.run_script('--configure', self.setup_input(output=' https://example.invalid/output '))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('不接受 URL', p.stderr)

    def test_invalid_credentials_and_mismatch_do_not_save(self):
        for kwargs in [dict(username=''), dict(username='   '), dict(username='a:b'),
                       dict(username='a\tb'), dict(username='a\rb'), dict(password='12345'),
                       dict(password='123456', password2='654321')]:
            p = self.run_script('--configure', self.setup_input(**kwargs))
            self.assertNotEqual(p.returncode, 0, kwargs)
            self.assertFalse(self.state.exists())

    def test_password_surrounding_spaces_are_not_trimmed(self):
        password = ' abcd '
        p = self.run_script('--configure', self.setup_input(username='a', password=password))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        c = json.loads((self.state / 'config.json').read_text())
        _, salt, digest = c['passwordHash'].split('$')
        self.assertEqual(hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=32).hex(), digest)
        self.assertNotIn(password, (self.state / 'config.json').read_text())

    def test_foreground_embedded_runtime_and_private_config(self):
        p = self.run_script('--foreground', self.setup_input())
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('CONFIG_OK', p.stdout)
        raw = (self.state / 'config.json').read_text()
        c = json.loads(raw)
        self.assertNotIn('correct-horse-battery', raw)
        self.assertEqual(c['repo'], str(self.project))
        self.assertEqual(c['port'], 43871)
        self.assertEqual(c['studioPort'], 43872)
        self.assertEqual(c['pollMs'], 60000)
        self.assertEqual(c['retentionHours'], 0)
        self.assertEqual(c['outputIdentity']['real'], str(self.output))
        self.assertEqual(c['outputIdentity']['dev'], self.output.stat().st_dev)
        self.assertEqual(c['retention'], {'mode':'manual'})
        self.assertTrue(c['passwordHash'].startswith('scrypt$'))
        self.assertEqual((self.state / 'config.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertTrue((self.state / 'bind-guard.cjs').is_file())
        self.assertFalse(self.plist.exists())

    def assert_no_service_mutations(self):
        calls = self.home / 'launchctl-calls'
        if calls.exists():
            for call in calls.read_text().splitlines():
                self.assertTrue(call.startswith('print '), call)

    def test_configure_only_saves_private_config_without_activation(self):
        p = self.run_script('--configure', self.setup_input())
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn('CONFIG_OK', p.stdout)
        self.assertIn('不启动任何服务', p.stdout)
        config = self.state / 'config.json'
        raw = config.read_text()
        c = json.loads(raw)
        self.assertNotIn('correct-horse-battery', raw)
        self.assertTrue(c['passwordHash'].startswith('scrypt$'))
        self.assertEqual(c['port'], 43871)
        self.assertEqual(c['studioPort'], 43872)
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        for name in ['runtime.cjs', 'bind-guard.cjs', 'studio-adapter.cjs']:
            self.assertTrue((self.state / name).is_file())
            self.assertEqual((self.state / name).stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.plist.exists())
        self.assertFalse((self.state / 'runtime.lock').exists())
        self.assert_no_service_mutations()

    def test_configure_refuses_live_runtime_without_killing_or_overwriting(self):
        # This process is the lock owner: a signal other than kill(pid, 0)
        # would break the test runner. No actual service is started.
        self.state.mkdir(parents=True)
        (self.state / 'runtime.lock').write_text(json.dumps({'pid': os.getpid()}))
        (self.state / 'config.json').write_text('original')
        (self.state / 'runtime.cjs').write_text('original runtime')
        p = self.run_script('--configure', self.setup_input())
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('未停止进程', p.stderr)
        self.assertEqual((self.state / 'config.json').read_text(), 'original')
        self.assertEqual((self.state / 'runtime.cjs').read_text(), 'original runtime')
        self.assert_no_service_mutations()

    def test_configure_refuses_loaded_service_even_without_lock(self):
        p = self.run_script('--configure', self.setup_input(), {'SERVICE_LOADED': '1'})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('未停止进程', p.stderr)
        self.assertFalse(self.state.exists())
        self.assert_no_service_mutations()

    def test_configure_refuses_existing_autostart_plist_without_modifying_it(self):
        self.plist.parent.mkdir(parents=True)
        self.plist.write_text('existing autostart')
        p = self.run_script('--configure', self.setup_input())
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.plist.read_text(), 'existing autostart')
        self.assertFalse(self.state.exists())
        self.assert_no_service_mutations()

    def test_configure_cancel_and_git_failure_do_not_save(self):
        for data, env in [(self.setup_input('NO'), {}), (self.setup_input(), {'GIT_FAIL': '1'})]:
            p = self.run_script('--configure', data, env)
            self.assertNotEqual(p.returncode, 0)
            self.assertFalse(self.state.exists())
            self.assert_no_service_mutations()

    def test_opt_in_retention_is_explicit(self):
        p = self.run_script('--foreground', self.setup_input(retention='24'))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('满 24 小时后永久删除', p.stdout)
        self.assertEqual(json.loads((self.state / 'config.json').read_text())['retentionHours'], 24)

    def test_invalid_retention_rejected(self):
        p = self.run_script('--foreground', self.setup_input(retention='-1'))
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(self.state.exists())

    def test_decline_does_not_save(self):
        p = self.run_script('--foreground', self.setup_input('NO'))
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(self.state.exists())

    def test_missing_output_not_created(self):
        missing = self.root / 'unmounted' / 'renders'
        p = self.run_script('--foreground', self.setup_input(output=missing))
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(missing.exists())
        self.assertFalse(self.state.exists())

    def test_detached_head_rejected(self):
        p = self.run_script('--foreground', extra_env={'DETACHED':'1'})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('detached HEAD', p.stderr)
        self.assertFalse(self.state.exists())

    def test_unowned_plist_never_overwritten_or_removed(self):
        self.plist.parent.mkdir(parents=True)
        self.plist.write_text('not ours')
        for action in ['--install', '--uninstall']:
            p = self.run_script(action, self.setup_input())
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(self.plist.read_text(), 'not ours')
        self.assertFalse(self.state.exists())

    def test_failed_git_read_does_not_save(self):
        p = self.run_script('--foreground', self.setup_input(), {'GIT_FAIL':'1'})
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(self.state.exists())
        self.assertIn('不会创建或修改 Git 凭据', p.stderr)

    def test_live_foreground_runtime_is_not_overwritten(self):
        self.state.mkdir(parents=True)
        (self.state / 'runtime.lock').write_text(json.dumps({'pid':os.getpid()}))
        (self.state / 'config.json').write_text('original')
        p = self.run_script('--foreground', self.setup_input())
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual((self.state / 'config.json').read_text(), 'original')

    def test_status_read_only(self):
        p = self.run_script('--status')
        self.assertEqual(p.returncode, 0)
        self.assertFalse(self.state.exists())

if __name__ == '__main__':
    unittest.main()
