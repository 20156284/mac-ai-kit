import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/service_manager.py'
spec = importlib.util.spec_from_file_location('service_manager', SCRIPT)
sm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm)


class ServiceManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / 'AI'
        self.models = self.home / 'Models with spaces'
        (self.root / 'bin').mkdir(parents=True)
        self.models.mkdir()
        (self.models / 'llm').mkdir()
        self.model = self.models / 'llm/test model.gguf'
        self.model.write_bytes(b'not loaded')
        for name in ('llama-server', 'llama-swap'):
            path = self.root / 'bin' / name
            path.write_text('#!/bin/sh\nexit 99\n')
            path.chmod(0o755)
        self.manager = sm.Manager(self.root, self.models, self.home)
        self.loaded = patch.object(
            sm.Manager, 'loaded', return_value=dict(loaded=False, owned=False, pid=None, detail='未加载')
        )
        self.loaded.start()
        self.addCleanup(self.loaded.stop)

    def configure(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.configure(str(self.model), **kwargs)

    def test_configure_does_not_launch_or_load(self):
        with (
            patch.object(sm.subprocess, 'run', side_effect=AssertionError('no execution')),
            patch.object(sm.os, 'execv', side_effect=AssertionError('no execution')),
        ):
            self.configure()
        cfg = json.loads(self.manager.config.read_text())
        self.assertEqual({'healthCheckTimeout', 'globalTTL', 'models'}, set(cfg))
        tokens = sm.shlex.split(cfg['models']['local-gguf']['cmd'])
        self.assertIn(str(self.model), tokens)
        self.assertIn('--offline', tokens)
        self.assertNotIn('hooks', cfg)
        self.assertFalse(self.manager.login.exists())
        self.assertFalse(self.manager.runtime.exists())
        self.manager.validate()

    def test_missing_models_never_creates_directory(self):
        other = self.home / 'missing-volume/models'
        mgr = sm.Manager(self.root, other, self.home)
        with self.assertRaises(sm.ServiceError):
            mgr.configure(self.model)
        self.assertFalse(other.exists())

    def test_reject_external_model_and_equal_ports(self):
        external = self.home / 'outside.gguf'
        external.write_bytes(b'')
        with self.assertRaises(sm.ServiceError):
            self.manager.configure(external)
        with self.assertRaises(sm.ServiceError):
            self.manager.configure(self.model, port=8080, backend_port=8080)

    def test_changed_model_directory_rejects_start_but_owns_previous_plist(self):
        self.configure()
        sm.atomic_write(self.manager.runtime, self.manager.plist())
        other = self.home / 'Other'
        other.mkdir()
        changed = sm.Manager(self.root, other, self.home)
        with self.assertRaises(sm.ServiceError):
            changed.validate()
        self.assertTrue(changed.owned_plist(changed.runtime))

    def test_configuration_tampering_hooks_rejected(self):
        self.configure()
        data = json.loads(self.manager.config.read_text())
        data['hooks'] = {'on_startup': {'preload': ['local-gguf']}}
        self.manager.config.write_text(json.dumps(data))
        with self.assertRaises(sm.ServiceError):
            self.manager.validate()

    def test_import_safe_and_reject_untrusted_command(self):
        self.configure()
        source = self.home / 'source.json'
        source.write_bytes(self.manager.config.read_bytes())
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.import_config(source, 8090)
        self.assertEqual(8090, self.manager.validate()['port'])
        cfg = json.loads(source.read_text())
        cfg['models']['local-gguf']['cmd'] += ' ; touch /tmp/unsafe'
        source.write_text(json.dumps(cfg))
        with self.assertRaises(sm.ServiceError):
            self.manager.import_config(source, 8080)

    def test_macro_in_filename_rejected(self):
        model = self.models / 'llm/${env.HOME}.gguf'
        model.write_bytes(b'')
        with self.assertRaises(sm.ServiceError):
            self.manager.configure(model)

    def test_plist_no_keepalive_uses_validation_wrapper(self):
        data = plistlib.loads(self.manager.plist())
        self.assertFalse(data['KeepAlive'])
        self.assertTrue(data['RunAtLoad'])
        self.assertEqual('run', data['ProgramArguments'][-1])
        self.assertNotIn('sudo', str(data))
        self.assertEqual('/usr/bin:/bin:/usr/sbin:/sbin', data['EnvironmentVariables']['PATH'])

    def test_login_enable_does_not_bootstrap_and_disable_does_not_stop(self):
        self.configure()
        with (
            patch.object(sm.sys, 'platform', 'darwin'),
            patch.object(self.manager, 'ctl', return_value=Mock(returncode=0)) as ctl,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.manager.enable_login()
            ctl.assert_called_once_with('enable', self.manager.target)
            self.assertTrue(self.manager.login.exists())
            self.manager.disable_login()
            self.assertFalse(self.manager.login.exists())
            self.assertEqual(1, ctl.call_count)

    def test_stop_refuses_foreign_service(self):
        with (
            patch.object(sm.sys, 'platform', 'darwin'),
            patch.object(
                self.manager, 'loaded', return_value=dict(loaded=True, owned=False, pid=123, detail='foreign')
            ),
            patch.object(self.manager, 'ctl') as ctl,
        ):
            with self.assertRaises(sm.ServiceError):
                self.manager.stop()
            ctl.assert_not_called()

    def test_stop_works_with_missing_model_directory(self):
        with (
            patch.object(sm.sys, 'platform', 'darwin'),
            patch.object(self.manager, 'paths_ready', side_effect=AssertionError('must not need mounted models')),
            patch.object(self.manager, 'loaded', return_value=dict(loaded=True, owned=True, pid=123, detail='owned')),
            patch.object(self.manager, 'ctl', return_value=Mock(returncode=0)) as ctl,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.manager.stop()
            ctl.assert_called_once_with('bootout', self.manager.target)

    def test_busy_port_prevents_bootstrap(self):
        self.configure()
        with (
            patch.object(sm.sys, 'platform', 'darwin'),
            patch.object(sm, 'available_port', side_effect=sm.ServiceError('busy')),
            patch.object(self.manager, 'ctl') as ctl,
        ):
            with self.assertRaises(sm.ServiceError):
                self.manager.start()
            ctl.assert_not_called()

    def test_start_only_owned_label_and_checks_health(self):
        self.configure()
        states = [dict(loaded=False, owned=False, pid=None), dict(loaded=True, owned=True, pid=321)]
        with (
            patch.object(sm.sys, 'platform', 'darwin'),
            patch.object(sm, 'available_port'),
            patch.object(self.manager, 'loaded', side_effect=states),
            patch.object(self.manager, 'health', return_value=True),
            patch.object(self.manager, 'ctl', return_value=Mock(returncode=0)) as ctl,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.manager.start()
        self.assertEqual(('enable', self.manager.target), ctl.call_args_list[0].args)
        self.assertEqual(('bootstrap', f'gui/{os.getuid()}', str(self.manager.runtime)), ctl.call_args_list[1].args)
        self.assertFalse(self.manager.login.exists())

    def test_run_only_proxy_with_verified_flags(self):
        self.configure()
        with (
            patch.object(sm.sys, 'platform', 'darwin'),
            patch.object(sm, 'available_port'),
            patch.object(sm.os, 'execv') as execute,
        ):
            self.manager.run()
        args = execute.call_args.args[1]
        self.assertEqual(
            [str(self.root / 'bin/llama-swap'), '-config', str(self.manager.config), '-listen', '127.0.0.1:8080'], args
        )

    def test_status_without_config_readonly_no_network(self):
        with (
            patch.object(self.manager, 'health', side_effect=AssertionError('no unrelated service probes')),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            self.manager.status()
        self.assertIn('未安装' if not sm.executable(self.root, 'llama-swap') else '可执行入口', out.getvalue())
        self.assertFalse(self.manager.base.exists())

    def test_health_uses_only_health_not_inference(self):
        response = Mock(status=200)
        response.read.return_value = b'OK'
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(sm.http.client, 'HTTPConnection', return_value=connection) as connect:
            self.assertTrue(self.manager.health(8080))
        connect.assert_called_once_with('127.0.0.1', 8080, timeout=1)
        connection.request.assert_called_once_with('GET', '/health')
        connection.close.assert_called_once()

    def test_noninteractive_mutation_requires_yes(self):
        with patch.object(sm.sys.stdin, 'isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()):
            code = sm.main(
                ['--root', str(self.root), '--models-dir', str(self.models), 'configure', '--model', str(self.model)]
            )
        self.assertEqual(2, code)
        self.assertFalse(self.manager.config.exists())

    def legacy_mlx(self):
        self.manager.base.mkdir(parents=True)
        self.manager.settings.write_text(
            json.dumps(
                dict(
                    models_dir=str(self.models),
                    model=str(self.models / 'llm/Qwen3.8-27B-bf16'),
                    server=str(self.root / 'bin/mlx-serve'),
                    port=8080,
                    backend_port=11234,
                    backend='mlx',
                    proxy_bind='127.0.0.1',
                )
            )
        )
        self.manager.config.write_text('{"models":{"local-mlx":{"cmd":"old command"}}}')
        return self.manager.settings.read_bytes(), self.manager.config.read_bytes()

    def test_mlx_configure_is_removed_without_writes(self):
        with self.assertRaisesRegex(sm.ServiceError, '桌面 App'):
            self.manager.configure(self.models / 'llm/Qwen3.8-27B-bf16', backend='mlx')
        self.assertFalse(self.manager.base.exists())
        self.assertFalse(hasattr(self.manager, 'mlx_config'))
        self.assertFalse(hasattr(self.manager, 'mlx_candidates'))

    def test_legacy_mlx_status_is_readonly_and_start_is_blocked(self):
        before = self.legacy_mlx()
        with (
            patch.object(self.manager, 'ctl', side_effect=AssertionError('no launchctl')),
            patch.object(sm.os, 'execv', side_effect=AssertionError('no exec')),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            self.manager.status()
        self.assertIn('旧版 MLX 托管配置已停用', out.getvalue())
        with (
            patch.object(sm.sys, 'platform', 'darwin'),
            patch.object(self.manager, 'ctl') as ctl,
            patch.object(sm.os, 'execv') as execute,
        ):
            for action in (self.manager.start, self.manager.run, self.manager.enable_login):
                with self.assertRaisesRegex(sm.ServiceError, '旧版 MLX'):
                    action()
            ctl.assert_not_called()
            execute.assert_not_called()
        self.assertEqual(before, (self.manager.settings.read_bytes(), self.manager.config.read_bytes()))

    def test_import_old_mlx_never_recreates_backend(self):
        self.legacy_mlx()
        with self.assertRaises(sm.ServiceError):
            self.manager.import_config(self.manager.config, 8080)

    def test_legacy_mlx_can_be_explicitly_stopped_without_model_validation(self):
        self.legacy_mlx()
        with (
            patch.object(sm.sys, 'platform', 'darwin'),
            patch.object(self.manager, 'loaded', return_value=dict(loaded=True, owned=True, pid=123)),
            patch.object(self.manager, 'ctl', return_value=Mock(returncode=0)) as ctl,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.manager.stop()
        ctl.assert_called_once_with('bootout', self.manager.target)

    def test_media_gguf_is_not_accepted_as_llm(self):
        audio = self.models / 'audio'
        audio.mkdir()
        file = audio / 'Qwen-ASR.gguf'
        file.write_bytes(b'not llm')
        with self.assertRaises(sm.ServiceError):
            self.manager.configure(file)

    def test_unmounted_volume_rejected_without_writes(self):
        manager = sm.Manager('/Volumes/Test/AI', '/Volumes/Test/models', self.home)
        with patch.object(Path, 'is_dir', return_value=True), patch.object(sm.os.path, 'ismount', return_value=False):
            with self.assertRaisesRegex(sm.ServiceError, '未挂载'):
                manager.paths_ready()

    def test_persisted_runtime_survives_original_location(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.configure(self.model)
        persisted = self.manager.bundle / 'service_manager.py'
        self.assertTrue(persisted.is_file())
        self.assertTrue((self.manager.bundle / 'path_validation.py').is_file())
        self.assertTrue((self.manager.bundle / 'catalog.json').is_file())
        result = subprocess.run(
            [sm.sys.executable, str(persisted), '--root', str(self.root), '--models-dir', str(self.models), 'status'],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('已通过托管结构', result.stdout)
        self.assertEqual(str(persisted), self.manager.program_args()[1])

    def legacy_settings(self):
        self.configure()
        data = json.loads(self.manager.settings.read_text())
        del data['proxy_bind']
        self.manager.settings.write_text(json.dumps(data))
        return self.manager.settings.read_bytes()

    def test_legacy_read_and_run_stay_loopback_without_write(self):
        before = self.legacy_settings()
        self.assertEqual('127.0.0.1', self.manager.validate()['proxy_bind'])
        with (
            patch.object(sm.sys, 'platform', 'darwin'),
            patch.object(sm, 'available_port') as ports,
            patch.object(sm.os, 'execv') as execute,
        ):
            self.manager.run()
        self.assertEqual('127.0.0.1:8080', execute.call_args.args[1][-1])
        self.assertEqual((8080, '127.0.0.1'), ports.call_args_list[0].args)
        self.assertEqual(before, self.manager.settings.read_bytes())

    def test_explicit_migration_preserves_config_model_ports_and_updates_runtime(self):
        self.legacy_settings()
        before = self.manager.config.read_bytes()
        data = self.manager.read_settings()
        with (
            patch.object(sm.subprocess, 'run', side_effect=AssertionError('no launch')),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.manager.set_bind('0.0.0.0')
        data['proxy_bind'] = '0.0.0.0'
        self.assertEqual(data, self.manager.validate())
        self.assertEqual(before, self.manager.config.read_bytes())
        self.assertEqual(SCRIPT.read_bytes(), (self.manager.bundle / 'service_manager.py').read_bytes())
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.set_bind('127.0.0.1')
        self.assertEqual('127.0.0.1', self.manager.validate()['proxy_bind'])

    def test_binding_validation_and_busy_service_do_not_write(self):
        self.configure()
        before = self.manager.settings.read_bytes()
        with self.assertRaises(sm.ServiceError):
            self.manager.set_bind('192.168.1.2')
        with patch.object(self.manager, 'loaded', return_value=dict(loaded=True)):
            with self.assertRaises(sm.ServiceError):
                self.manager.set_bind('127.0.0.1')
        self.assertEqual(before, self.manager.settings.read_bytes())
        for field, value in [('proxy_bind', '::'), ('proxy_bind', None), ('extra', True)]:
            data = json.loads(before)
            data[field] = value
            self.manager.settings.write_text(json.dumps(data))
            with self.assertRaises(sm.ServiceError):
                self.manager.validate()

    def test_wildcard_port_probe_and_backend_loopback(self):
        self.configure(bind='0.0.0.0')
        with (
            patch.object(sm.sys, 'platform', 'darwin'),
            patch.object(sm, 'available_port') as ports,
            patch.object(sm.os, 'execv'),
        ):
            self.manager.run()
        self.assertEqual([(8080, '0.0.0.0'), (18081,)], [call.args for call in ports.call_args_list])
        sock = Mock()
        sock.__enter__ = Mock(return_value=sock)
        sock.__exit__ = Mock(return_value=False)
        with patch.object(sm.socket, 'socket', return_value=sock):
            sm.available_port(8080, '0.0.0.0')
        sock.bind.assert_called_once_with(('0.0.0.0', 8080))

    def test_lan_status_has_correct_client_addresses(self):
        self.configure(bind='0.0.0.0')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.manager.status()
        text = out.getvalue()
        self.assertIn('0.0.0.0:8080', text)
        self.assertIn('http://127.0.0.1:8080/v1', text)
        self.assertIn('http://<这台 Mac 的局域网 IP>:8080/v1', text)
        self.assertNotIn('http://0.0.0.0', text)
        self.assertIn('未配置认证或 TLS', text)

    def test_set_bind_cli_requires_explicit_bind_and_confirmation(self):
        self.legacy_settings()
        before = self.manager.settings.read_bytes()
        prefix = ['--root', str(self.root), '--models-dir', str(self.models), 'set-bind']
        with (
            patch.object(sm.sys.stdin, 'isatty', return_value=False),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(2, sm.main(prefix + ['--yes']))
            self.assertEqual(2, sm.main(prefix + ['--proxy-bind', '0.0.0.0']))
            self.assertEqual(before, self.manager.settings.read_bytes())
            self.assertEqual(0, sm.main(prefix + ['--proxy-bind', '0.0.0.0', '--yes']))
        self.assertEqual('0.0.0.0', self.manager.validate()['proxy_bind'])

    def test_start_cannot_silently_override_saved_bind(self):
        self.legacy_settings()
        with (
            patch.object(self.manager, 'start', side_effect=AssertionError('must not start')),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = sm.main(
                [
                    '--root',
                    str(self.root),
                    '--models-dir',
                    str(self.models),
                    'start',
                    '--proxy-bind',
                    '0.0.0.0',
                    '--yes',
                ]
            )
        self.assertEqual(2, code)
        self.assertEqual('127.0.0.1', self.manager.read_settings()['proxy_bind'])

    def test_menu_explicit_bind_migration_without_reconfigure(self):
        self.legacy_settings()
        with (
            patch.object(sm, 'select_menu', side_effect=['8', '0']),
            patch('builtins.input', side_effect=['2', '1', '']),
            patch.object(sm, 'confirm', return_value=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            sm.menu(self.manager)
        self.assertEqual('0.0.0.0', self.manager.validate()['proxy_bind'])

    def test_path_edge_whitespace_rejected(self):
        with self.assertRaises(ValueError):
            sm.Manager('/tmp/test /AI', self.models)


if __name__ == '__main__':
    unittest.main()
