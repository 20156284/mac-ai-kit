"""离线小文件验证 H3 差分复用，不联网或下载真实模型。"""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('reuse_downloader', Path(__file__).resolve().parents[1] / 'scripts/download_models.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def model(name, files, family='h3'):
    return {'id': name, 'repo_id': 'owner/' + name, 'reuse_family': family,
            'expected_files': {p: {'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()} for p, b in files.items()}}


class H3ReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'FL2VA'
        self.source.mkdir()
        self.target = self.root / 'REF2VA'
        self.shared = b'common verified weights'
        self.a = model('FL2VA', {'weight.safetensors': self.shared, 'config.json': b'A'})
        self.b = model('REF2VA', {'weight.safetensors': self.shared, 'config.json': b'B'})
        (self.source / 'weight.safetensors').write_bytes(self.shared)
        (self.source / 'config.json').write_bytes(b'A')

    def tearDown(self):
        self.tmp.cleanup()

    def fake_download(self, args, check):
        stage = Path(args[args.index('--local-dir') + 1])
        includes = [args[i + 1] for i, item in enumerate(args) if item == '--include']
        self.assertEqual(includes, ['config.json'])
        (stage / 'config.json').write_bytes(b'B')

    def test_verified_reuse_downloads_only_difference_preserves_source(self):
        with mock.patch.object(m.subprocess, 'run', side_effect=self.fake_download) as run:
            inventory = m.download_verified_bundle('ms-hub', self.b, self.target, 4, [self.source], [self.a, self.b])
        self.assertEqual(run.call_count, 1)
        self.assertTrue((self.target / 'weight.safetensors').is_symlink())
        self.assertFalse(Path((self.target / 'weight.safetensors').readlink()).is_absolute())
        self.assertEqual((self.target / 'weight.safetensors').read_bytes(), self.shared)
        self.assertEqual((self.source / 'config.json').read_bytes(), b'A')
        self.assertIn('resolved_source', inventory[0])
        with mock.patch.object(m.subprocess, 'run') as run:
            m.download_verified_bundle('ms-hub', self.b, self.target, 4, [self.source], [self.a, self.b])
            run.assert_not_called()

    def test_source_wrong_hash_not_reused(self):
        (self.source / 'weight.safetensors').write_bytes(b'x' * len(self.shared))
        self.assertEqual(m.reusable_files(self.b, self.target, [self.source], [self.a, self.b]), {})
        self.assertEqual((self.source / 'weight.safetensors').read_bytes(), b'x' * len(self.shared))

    def test_unrelated_family_not_reused(self):
        unrelated = model('audio', {'audio.bin': self.shared}, 'audio')
        (self.source / 'weight.safetensors').unlink()
        (self.source / 'audio.bin').write_bytes(self.shared)
        self.assertEqual(m.reusable_files(self.b, self.target, [self.source], [unrelated, self.b]), {})

    def test_same_path_skipped_nested_paths_rejected(self):
        self.assertEqual(m.reusable_files(self.b, self.source, [self.source], [self.a, self.b]), {})
        self.assertEqual(m.reusable_files(self.b, self.target, [self.target], [self.a, self.b]), {})
        for dest in [self.source / 'nested', self.root]:
            with self.assertRaisesRegex(ValueError, '相互嵌套'):
                m.reusable_files(self.b, dest, [self.source], [self.a, self.b])

    def test_target_parent_link_rejected(self):
        link = self.root / 'linked'
        link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            m.download_verified_bundle('ms-hub', self.b, link / 'target', 4, [], [self.b])

    def test_broken_link_and_corrupt_target_never_overwritten(self):
        self.target.mkdir()
        path = self.target / 'weight.safetensors'
        for kind in ['broken', 'loop', 'corrupt']:
            if path.is_symlink() or path.exists():
                path.unlink()
            if kind == 'corrupt':
                path.write_bytes(b'bad')
            else:
                path.symlink_to('missing' if kind == 'broken' else 'weight.safetensors')
            with mock.patch.object(m.subprocess, 'run') as run, self.assertRaisesRegex(ValueError, '不覆盖'):
                m.download_verified_bundle('ms-hub', self.b, self.target, 4, [self.source], [self.a, self.b])
            run.assert_not_called()

    def test_traversal_specs_rejected(self):
        for name in ['../escape', '/escape', 'a/../b', 'a//b', 'a\\b']:
            bad = model('bad', {name: b'x'})
            with self.assertRaises(ValueError):
                m.verified_specs(bad)

    def test_bad_download_never_assembled(self):
        def corrupt(args, check):
            (Path(args[args.index('--local-dir') + 1]) / 'config.json').write_bytes(b'X')
        with mock.patch.object(m.subprocess, 'run', side_effect=corrupt), self.assertRaisesRegex(ValueError, 'SHA256'):
            m.download_verified_bundle('ms-hub', self.b, self.target, 4, [self.source], [self.a, self.b])
        self.assertEqual(list(self.target.iterdir()), [])

    def test_two_selected_size_deduplicates_shared_sha(self):
        total = m.reuse_estimate([self.a, self.b], [(self.a, self.source, []), (self.b, self.target, [])], [], [self.a, self.b], verify_sources=False)
        self.assertEqual(total, len(self.shared) + 2)

    def test_source_broken_link_requires_explicit_repair(self):
        path = self.source / 'weight.safetensors'
        path.unlink()
        path.symlink_to('gone')
        with self.assertRaisesRegex(ValueError, '断链'):
            m.reusable_files(self.b, self.target, [self.source], [self.a, self.b])

    def test_two_downloads_verify_first_then_link_second(self):
        first = self.root / 'new-FL2VA'
        def download(args, check):
            stage = Path(args[args.index('--local-dir') + 1])
            includes = [args[i + 1] for i, item in enumerate(args) if item == '--include']
            if 'owner/FL2VA' in args:
                self.assertIn('weight.safetensors', includes)
                (stage / 'weight.safetensors').write_bytes(self.shared)
                (stage / 'config.json').write_bytes(b'A')
            else:
                self.assertEqual(includes, ['config.json'])
                (stage / 'config.json').write_bytes(b'B')
        with mock.patch.object(m.subprocess, 'run', side_effect=download):
            m.download_verified_bundle('ms-hub', self.a, first, 4, [], [self.a, self.b])
            m.download_verified_bundle('ms-hub', self.b, self.target, 4, [first], [self.a, self.b])
        self.assertEqual((self.target / 'weight.safetensors').resolve(), first / 'weight.safetensors')

    def test_estimate_subtracts_verified_target_and_rejects_corruption(self):
        self.assertEqual(m.reuse_estimate([self.a], [(self.a, self.source, [])], [], [self.a]), 0)
        (self.source / 'config.json').write_bytes(b'X')
        with self.assertRaisesRegex(ValueError, '不覆盖'):
            m.reuse_estimate([self.a], [(self.a, self.source, [])], [], [self.a])

    def test_interrupt_preserves_stage_then_resumes_without_redownloading(self):
        stages = []
        def interrupted(args, check):
            stage = Path(args[args.index('--local-dir') + 1])
            stages.append(stage)
            (stage / 'weight.safetensors').write_bytes(self.shared)
            raise KeyboardInterrupt()
        with mock.patch.object(m.subprocess, 'run', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            m.download_verified_bundle('ms-hub', self.b, self.target, 4, [], [self.b])
        self.assertEqual((stages[0] / 'weight.safetensors').read_bytes(), self.shared)
        self.assertEqual(m.reuse_estimate([self.b], [(self.b, self.target, [])], [], [self.b]), 1)
        with mock.patch.object(m.subprocess, 'run', side_effect=self.fake_download):
            m.download_verified_bundle('ms-hub', self.b, self.target, 4, [], [self.b])
        self.assertFalse(stages[0].exists())
        self.assertEqual((self.target / 'weight.safetensors').read_bytes(), self.shared)

    def test_unowned_or_symlink_staging_rejected_without_mutation(self):
        stage, _ = m.staging_identity(self.b, self.target)
        stage.mkdir()
        sentinel = stage / 'keep'
        sentinel.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, '所有权'):
            m.staging_directory(self.b, self.target, create=True)
        self.assertEqual(sentinel.read_bytes(), b'keep')
        sentinel.unlink()
        stage.rmdir()
        stage.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            m.staging_directory(self.b, self.target, create=True)
        self.assertEqual((self.source / 'config.json').read_bytes(), b'A')

    def test_same_repository_components_can_share_target(self):
        one = {'id': 'one', 'repo_id': 'owner/repo', 'revision': 'commit'}
        two = dict(one, id='two')
        m.validate_plan_targets([(one, self.target, []), (two, self.target, [])])

    def test_different_repository_revision_or_h3_cannot_share_target(self):
        one = {'id': 'one', 'repo_id': 'owner/repo', 'revision': 'commit'}
        for two in [dict(one, repo_id='owner/other'), dict(one, revision='other'), dict(one, reuse_family='h3')]:
            with self.assertRaises(ValueError):
                m.validate_plan_targets([(one, self.target, []), (two, self.target, [])])


if __name__ == '__main__':
    unittest.main()
