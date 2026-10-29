"""增量下载回归：小文件和模拟容量，不联网、不下载真实模型。"""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import download_models as m


def model(key='main', relative='llm/Main', payload=b'known model'):
    return {
        'id': key,
        'repo_id': 'owner/Main' if key == 'main' else 'owner/' + key,
        'subdir': relative,
        'expected_files': {'weights.bin': {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}},
    }


class IncrementalDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / 'models'
        self.base.mkdir()
        self.model = model()
        self.folder = self.base / self.model['subdir']

    def tearDown(self):
        self.temp.cleanup()

    def write(self, folder, content=b'known model'):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'weights.bin'
        path.write_bytes(content)
        return path

    def plan(self, entry=None, folder=None, **kwargs):
        entry, folder = entry or self.model, folder or self.folder
        return m.download_preflight([(entry, folder, [])], [], [entry], base=self.base, **kwargs)[0]

    def test_canonical_existing_not_strict_skips_network(self):
        self.write(self.folder)
        row = self.plan()
        self.assertEqual(row['additional'], 0)
        with mock.patch.object(m.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            m.download_verified_bundle('unused', self.model, self.folder, 1, [], [self.model], row['local_reuse'])
        run.assert_not_called()

    def test_legacy_repo_directory_is_preserved_without_implicit_migration(self):
        source = self.write(self.base / 'owner/Main')
        row = self.plan()
        with mock.patch.object(m.subprocess, 'run') as run, self.assertRaisesRegex(ValueError, '旧目录文件保持原样'):
            m.download_verified_bundle('unused', self.model, self.folder, 1, [], [self.model], row['local_reuse'])
        run.assert_not_called()
        self.assertTrue(source.exists())
        self.assertFalse((self.folder / 'weights.bin').exists())

    def test_capacity_main_exists_mtp_final_085gb_peak_170gb(self):
        # 容量数字来自报告案例，文件核验通过模拟注入，绝不创建数十 GB 文件。
        for relative in ('llm/Main', 'owner/Main'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                main = model()
                main['expected_files']['weights.bin']['bytes'] = 54_743_807_404
                self.write(root / relative)
                head = model('mtp', 'llm/Main/mtp')
                head['expected_files']['weights.bin']['bytes'] = 849_400_335
                head['local_adaptations'] = [{'source': 'weights.bin', 'target': 'compatible.bin'}]
                head['generated_expected_files'] = {'compatible.bin': {'bytes': 849_400_400}}
                head['final_only_adaptation'] = True
                plans = [(head, root / head['subdir'], []), (main, root / main['subdir'], [])]
                with mock.patch.object(
                    m, 'verify_file', side_effect=lambda path, spec, strict=False: spec['bytes'] == 54_743_807_404
                ):
                    rows = m.download_preflight(plans, [], [head, main], base=root)
                self.assertEqual(rows[1]['existing'], 54_743_807_404)
                self.assertEqual(sum(r['network'] for r in rows), 849_400_335)
                needed = sum(r['additional'] for r in rows)
                self.assertEqual(needed, 849_400_400)
                self.assertEqual(rows[0]['peak_additional'], 1_698_800_735)
                self.assertLess(needed, 31_600_000_000)

    def test_no_existing_uses_missing_sum_not_entire_catalog_estimate(self):
        self.model['disk_gb'] = 99
        row = self.plan()
        self.assertEqual(row['additional'], len(b'known model'))
        self.assertEqual(row['missing'], ['weights.bin'])

    def test_existing_subset_downloads_only_missing_files(self):
        self.write(self.folder)
        self.model['expected_files']['config.json'] = {'bytes': 2, 'sha256': hashlib.sha256(b'{}').hexdigest()}
        row = self.plan()
        self.assertEqual(row['network'], 2)

        def fake_download(args, check):
            includes = [args[i + 1] for i, arg in enumerate(args) if arg == '--include']
            self.assertEqual(includes, ['config.json'])
            stage = Path(args[args.index('--local-dir') + 1])
            (stage / 'config.json').write_bytes(b'{}')

        with (
            mock.patch.object(m.subprocess, 'run', side_effect=fake_download),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            m.download_verified_bundle('unused', self.model, self.folder, 1, [], [self.model])

    def test_completed_stage_deducted_without_network(self):
        self.folder.parent.mkdir(parents=True)
        stage = m.staging_directory(self.model, self.folder, create=True)
        self.write(stage)
        self.assertEqual(self.plan()['additional'], 0)
        with mock.patch.object(m.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            m.download_verified_bundle('unused', self.model, self.folder, 1, [], [self.model])
        run.assert_not_called()

    def test_target_corruption_is_explicit_and_preserved(self):
        path = self.write(self.folder, b'corrupt')
        with self.assertRaisesRegex(ValueError, '不覆盖'):
            self.plan()
        self.assertEqual(path.read_bytes(), b'corrupt')

    def test_legacy_corruption_reported_without_overwrite(self):
        path = self.write(self.base / 'Main', b'corrupt')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            row = self.plan(report=True)
        self.assertIn('旧文件校验不符', output.getvalue())
        self.assertGreater(row['network'], 0)
        self.assertEqual(path.read_bytes(), b'corrupt')

    def test_discovery_does_not_follow_directory_links_or_unknown_deep_paths(self):
        outside = Path(self.temp.name) / 'outside'
        self.write(outside)
        (self.base / 'Main').symlink_to(outside, target_is_directory=True)
        self.write(self.base / 'unknown/deeper/location/Main')
        row = self.plan()
        self.assertEqual(row['local_reuse'], {})
        self.assertGreater(row['network'], 0)

    def test_preview_and_list_never_hash(self):
        self.write(self.folder)
        with mock.patch.object(m, 'digest_file', side_effect=AssertionError('禁止读取权重')):
            row = self.plan(verify_sources=False)
        self.assertEqual(row['network'], len(b'known model'))

    def test_regular_target_link_refused_before_hashing(self):
        outside = self.write(Path(self.temp.name) / 'outside')
        self.folder.mkdir(parents=True)
        (self.folder / 'weights.bin').symlink_to(outside)
        with mock.patch.object(m, 'digest_file', side_effect=AssertionError('不得读取链接目标')):
            with self.assertRaisesRegex(ValueError, '不跟随'):
                self.plan()

    def test_main_checks_only_missing_bytes_before_download(self):
        self.write(self.folder)
        self.model['disk_gb'] = 56.4
        self.model['expected_files']['config.json'] = {'bytes': 2, 'sha256': hashlib.sha256(b'{}').hexdigest()}
        catalog = Path(self.temp.name) / 'catalog.json'
        catalog.write_text(json.dumps({'schema_version': 1, 'models': [self.model]}))
        cli = Path(self.temp.name) / 'client'
        cli.write_text('模拟客户端')
        cli.chmod(0o700)

        def fake_download(args, check):
            includes = [args[i + 1] for i, arg in enumerate(args) if arg == '--include']
            self.assertEqual(includes, ['config.json'])
            (Path(args[args.index('--local-dir') + 1]) / 'config.json').write_bytes(b'{}')

        with (
            mock.patch.object(m.platform, 'system', return_value='Darwin'),
            mock.patch.object(m.shutil, 'disk_usage', return_value=mock.Mock(free=2)),
            mock.patch.object(m.subprocess, 'run', side_effect=fake_download) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                m.main(['--catalog', str(catalog), '--select', 'main', '--dest', str(self.base), '--cli', str(cli)]), 0
            )
        self.assertEqual(run.call_count, 1)
        self.assertEqual((self.folder / 'config.json').read_bytes(), b'{}')

    def test_all_enabled_catalog_models_have_verified_incremental_specs(self):
        for entry in m.load_catalog(ROOT / 'catalog.json'):
            if entry.get('enabled', True) and entry.get('repo_id'):
                self.assertTrue(m.verified_specs(entry))

    def test_complete_main_command_works_without_download_client(self):
        self.write(self.folder)
        catalog = Path(self.temp.name) / 'catalog.json'
        catalog.write_text(json.dumps({'schema_version': 1, 'models': [self.model]}))
        output = io.StringIO()
        with (
            mock.patch.object(m.platform, 'system', return_value='Darwin'),
            mock.patch.object(m.subprocess, 'run') as run,
            contextlib.redirect_stdout(output),
        ):
            result = m.main(
                [
                    '--catalog',
                    str(catalog),
                    '--select',
                    'main',
                    '--dest',
                    str(self.base),
                    '--cli',
                    '/nonexistent/client',
                ]
            )
        self.assertEqual(result, 0)
        run.assert_not_called()
        self.assertIn('无需重复下载', output.getvalue())
        self.assertIn('本次新增 0.00 GB', output.getvalue())


if __name__ == '__main__':
    unittest.main()
