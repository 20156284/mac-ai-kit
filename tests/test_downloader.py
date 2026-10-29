"""离线单元测试；不使用第三方包、不联网、不下载模型、不做推理。"""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/download_models.py'
spec = importlib.util.spec_from_file_location('download_models', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cat = self.root / 'catalog.json'
        self.cat.write_text(
            json.dumps(
                {
                    'schema_version': 1,
                    'models': [{'id': 'test', 'repo_id': 'owner/test', 'revision': 'fixed-test-revision'}],
                }
            )
        )
        self.cli = self.root / 'ms-hub'
        self.cli.write_text('# 模拟可执行文件，从不启动\n')
        self.cli.chmod(0o700)
        self.args = [
            '--catalog',
            str(self.cat),
            '--dest',
            str(self.root / 'models'),
            '--select',
            'test',
            '--cli',
            str(self.cli),
        ]

    def tearDown(self):
        self.temp.cleanup()

    def run_quiet(self, args):
        with contextlib.redirect_stdout(io.StringIO()):
            return m.main(args)

    def test_dry_run_no_process_no_write(self):
        with mock.patch.object(m.subprocess, 'run') as run:
            self.assertEqual(self.run_quiet(self.args + ['--dry-run', '--details']), 0)
            run.assert_not_called()
        self.assertFalse((self.root / 'models').exists())

    def test_list_no_process(self):
        with mock.patch.object(m.subprocess, 'run') as run:
            self.assertEqual(self.run_quiet(['--catalog', str(self.cat), '--list']), 0)
            run.assert_not_called()

    def test_shared_models_dir_alias_with_spaces(self):
        shared = self.root / 'Independent shared models'
        args = ['--catalog', str(self.cat), '--models-dir', str(shared), '--select', 'test', '--dry-run', '--details']
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(m.subprocess, 'run') as run:
            self.assertEqual(m.main(args), 0)
            run.assert_not_called()
        result = json.loads(output.getvalue()[output.getvalue().index('{\n') :])
        self.assertEqual(result['models_dir'], str(shared))
        command = result['commands'][0]
        self.assertEqual(command[command.index('--local-dir') + 1], str(shared / 'owner/test'))
        self.assertFalse(shared.exists())

    def test_shared_models_dir_and_dest_conflict(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            m.main(self.args + ['--models-dir', str(self.root / 'other'), '--dry-run', '--details'])
        self.assertEqual(error.exception.code, 2)

    def test_shared_default_is_outside_toolkit(self):
        output = io.StringIO()
        with mock.patch.object(m.sys.stdin, 'isatty', return_value=False), contextlib.redirect_stdout(output):
            self.assertEqual(m.main(['--catalog', str(self.cat), '--select', 'test', '--dry-run', '--details']), 0)
        result = json.loads(output.getvalue()[output.getvalue().index('{\n') :])
        self.assertEqual(result['models_dir'], str(Path('~/AI/models').expanduser().resolve()))

    def test_catalog_paths_remain_under_shared_root(self):
        shared = self.root / 'shared'
        models = m.load_catalog(SCRIPT.parents[1] / 'catalog.json')
        targets = [
            m.target_path(shared, model.get('subdir') or model['repo_id'])
            for model in models
            if model.get('enabled', True) and model.get('repo_id')
        ]
        m.validate_plan_targets(
            [
                (model, m.target_path(shared, model.get('subdir') or model['repo_id']), [])
                for model in models
                if model.get('enabled', True) and model.get('repo_id')
            ]
        )
        self.assertTrue(all(target.is_relative_to(shared) for target in targets))
        self.assertFalse(shared.exists())

    def test_non_mac_blocks_download(self):
        with mock.patch.object(m.platform, 'system', return_value='Linux'):
            with self.assertRaisesRegex(ValueError, 'macOS'):
                self.run_quiet(self.args + ['--yes', '--accept-licenses'])
        self.assertFalse((self.root / 'models').exists())

    def test_repo_injection_rejected(self):
        for repo in ['../a', '-flag/x', 'https://site/a', 'a/b/c', 'a/$(id)', 'a/b;cmd']:
            with self.assertRaises(ValueError):
                m.safe_repo(repo)

    def test_traversal_and_symlink_rejected(self):
        for path in ['../escape', '/tmp/escape', 'valid/../../escape']:
            with self.assertRaises(ValueError):
                m.target_path(self.root, path)
        (self.root / 'link').symlink_to('/tmp')
        with self.assertRaises(ValueError):
            m.target_path(self.root, 'link/escape')

    def test_argv_not_shell(self):
        args = m.command(self.cli, {'repo_id': 'a/b', 'include': ['*.safetensors']}, self.root / 'with spaces', 4)
        self.assertIn(str(self.root / 'with spaces'), args)
        self.assertEqual(args[-2:], ['--include', '*.safetensors'])

    def test_success_manifest_hash(self):
        def fake_run(args, check):
            folder = Path(args[args.index('--local-dir') + 1])
            folder.mkdir(parents=True)
            (folder / 'weights.test').write_bytes(b'test bytes only')
            return subprocess.CompletedProcess(args, 0)

        with (
            mock.patch.object(m.platform, 'system', return_value='Darwin'),
            mock.patch.object(m.subprocess, 'run', side_effect=fake_run),
        ):
            self.assertEqual(self.run_quiet(self.args + ['--yes', '--accept-licenses', '--sha256']), 0)
        record = json.loads(next((self.root / 'models/Mac-AI-Manifests').glob('*.json')).read_text())
        self.assertEqual(record['status'], 'downloaded')
        self.assertIsNone(record['models'][0]['resolved_commit'])
        self.assertEqual(len(record['models'][0]['files'][0]['sha256']), 64)

    def test_failure_manifest(self):
        with (
            mock.patch.object(m.platform, 'system', return_value='Darwin'),
            mock.patch.object(m.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['mock'])),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_quiet(self.args + ['--yes', '--accept-licenses'])
        record = json.loads(next((self.root / 'models/Mac-AI-Manifests').glob('*.json')).read_text())
        self.assertEqual(record['status'], 'incomplete')

    def test_dependencies_deduplicated_ordered(self):
        shared = {'id': 'shared', 'repo_id': 'owner/shared'}
        a = {'id': 'a', 'repo_id': 'owner/a', 'requires': ['shared']}
        b = {'id': 'b', 'repo_id': 'owner/b', 'requires': ['shared']}
        self.assertEqual([v['id'] for v in m.expand_dependencies([a, b], [a, b, shared])], ['shared', 'a', 'b'])

    def test_dependencies_invalid(self):
        for models in [
            [{'id': 'a', 'repo_id': 'o/a', 'requires': ['unknown']}],
            [{'id': 'a', 'repo_id': 'o/a', 'requires': ['b']}, {'id': 'b', 'repo_id': 'o/b', 'enabled': False}],
            [{'id': 'a', 'repo_id': 'o/a', 'requires': ['b']}, {'id': 'b', 'repo_id': 'o/b', 'requires': ['a']}],
        ]:
            with self.assertRaises(ValueError):
                m.expand_dependencies([models[0]], models)

    def test_dependency_directory_collision(self):
        self.cat.write_text(
            json.dumps(
                {
                    'schema_version': 1,
                    'models': [
                        {'id': 'a', 'repo_id': 'o/a', 'subdir': 'same', 'requires': ['b']},
                        {'id': 'b', 'repo_id': 'o/b', 'subdir': 'same'},
                    ],
                }
            )
        )
        args = [
            '--catalog',
            str(self.cat),
            '--dest',
            str(self.root / 'models'),
            '--select',
            'a',
            '--dry-run',
            '--details',
        ]
        with self.assertRaisesRegex(ValueError, '重复'):
            self.run_quiet(args)

    def test_download_starts_without_extra_confirmation(self):
        def fake_run(args, check):
            folder = Path(args[args.index('--local-dir') + 1])
            folder.mkdir(parents=True)
            (folder / 'weight').write_bytes(b'test')

        with (
            mock.patch.object(m.platform, 'system', return_value='Darwin'),
            mock.patch.object(m.sys.stdin, 'isatty', return_value=False),
            mock.patch.object(m.subprocess, 'run', side_effect=fake_run),
            mock.patch('builtins.input') as ask,
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(m.main(self.args), 0)
            self.assertIn('可用磁盘', output.getvalue())
            for unwanted in ('本次下载（已包含', '下载到：', '许可核查', '正在下载', '确认已检查'):
                self.assertNotIn(unwanted, output.getvalue())
            ask.assert_not_called()
        self.assertTrue((self.root / 'models').exists())


if __name__ == '__main__':
    unittest.main()
