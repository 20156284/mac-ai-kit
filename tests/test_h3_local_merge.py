"""微型离线测试数据；不下载权重、不联网、不执行客户端或模型。"""

import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('h3_local_merge', ROOT / 'scripts/merge_h3_models.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class LocalMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve() / 'models'
        self.data = b'shared tiny fixture'

        def metadata(data):
            return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

        self.models = [
            {
                'id': key,
                'subdir': 'video/' + variant,
                'expected_files': {
                    'common.bin': metadata(self.data),
                    'nested/shared.bin': metadata(self.data),
                    'transformer.bin': metadata(variant.encode()),
                },
            }
            for key, variant in zip(m.IDS, ('FL2VA', 'REF2VA'))
        ]
        self.a, self.b = [self.base / model['subdir'] for model in self.models]
        for model, folder in zip(self.models, (self.a, self.b)):
            (folder / 'nested').mkdir(parents=True)
            (folder / 'common.bin').write_bytes(self.data)
            (folder / 'nested/shared.bin').write_bytes(self.data)
            (folder / 'transformer.bin').write_bytes(folder.name.encode())

    def tearDown(self):
        self.tmp.cleanup()

    def merge(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return m.merge(self.base, self.models, **kwargs)

    def test_merge_preserves_source_variants_and_is_idempotent(self):
        result = self.merge()
        self.assertEqual(result['merged'], 2)
        self.assertEqual(result['bytes_saved'], 2 * len(self.data))
        for rel in ('common.bin', 'nested/shared.bin'):
            self.assertFalse((self.a / rel).is_symlink())
            self.assertTrue((self.b / rel).is_symlink())
            self.assertFalse(os.path.isabs(os.readlink(self.b / rel)))
            self.assertEqual((self.b / rel).read_bytes(), self.data)
        self.assertEqual((self.b / 'transformer.bin').read_bytes(), b'REF2VA')
        self.assertEqual(self.merge()['already_shared'], 2)
        self.assertFalse(list(self.base.rglob('.h3-merge-*')))

    def test_readonly_preview(self):
        before = [(p, p.lstat()) for p in self.base.rglob('*')]
        self.assertEqual(self.merge(dry_run=True)['merged'], 2)
        for path, info in before:
            self.assertEqual(m.signature(path.lstat()), m.signature(info))
        self.assertFalse(list(self.base.rglob('.h3-merge-*')))

    def test_same_size_corrupt_target_not_replaced(self):
        bad = b'x' * len(self.data)
        (self.b / 'common.bin').write_bytes(bad)
        self.assertEqual(self.merge()['skipped'], 1)
        self.assertEqual((self.b / 'common.bin').read_bytes(), bad)
        self.assertFalse((self.b / 'common.bin').is_symlink())

    def test_size_mismatch_and_missing_file_not_created(self):
        (self.b / 'common.bin').write_bytes(b'x')
        (self.b / 'nested/shared.bin').unlink()
        self.assertEqual(self.merge()['skipped'], 2)
        self.assertFalse((self.b / 'nested/shared.bin').exists())

    def test_corrupt_first_source_can_never_replace_good_second(self):
        (self.a / 'common.bin').write_bytes(b'x' * len(self.data))
        self.merge()
        self.assertFalse((self.b / 'common.bin').is_symlink())
        self.assertEqual((self.b / 'common.bin').read_bytes(), self.data)

    def test_legacy_folder_and_no_recursive_scan(self):
        self.a.rename(self.base / m.LEGACY)
        with mock.patch.object(Path, 'rglob', side_effect=AssertionError('no recursive scan')):
            result = self.merge()
        self.assertEqual(result['merged'], 2)
        self.assertFalse((self.b / 'common.bin').is_symlink())
        self.assertTrue((self.base / m.LEGACY / 'common.bin').is_symlink())

    def test_missing_counterpart_no_download_or_mkdir(self):
        import shutil

        shutil.rmtree(self.b)
        with mock.patch.object(os, 'mkdir', side_effect=AssertionError('no mkdir')):
            self.assertEqual(self.merge()['merged'], 0)
        self.assertFalse(self.b.exists())

    def test_external_directory_link_rejected(self):
        outside = Path(self.tmp.name) / 'outside'
        self.b.rename(outside)
        self.b.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.merge()['merged'], 0)
        self.assertFalse((outside / 'common.bin').is_symlink())

    def test_external_file_and_cycle_links_rejected(self):
        outside = Path(self.tmp.name) / 'outside.bin'
        outside.write_bytes(self.data)
        (self.b / 'common.bin').unlink()
        (self.b / 'common.bin').symlink_to(outside)
        (self.b / 'nested/shared.bin').unlink()
        (self.b / 'nested/shared.bin').symlink_to('shared.bin')
        self.assertEqual(self.merge()['skipped'], 2)
        self.assertEqual(outside.read_bytes(), self.data)

    def test_symlink_parent_rejected(self):
        outside = Path(self.tmp.name) / 'outside'
        (self.b / 'nested').rename(outside)
        (self.b / 'nested').symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.merge()['skipped'], 1)
        self.assertFalse((outside / 'shared.bin').is_symlink())

    def test_failed_replace_leaves_original(self):
        with mock.patch.object(m.os, 'replace', side_effect=OSError('injected failure')):
            self.assertEqual(self.merge()['skipped'], 2)
        self.assertEqual((self.b / 'common.bin').read_bytes(), self.data)
        self.assertFalse(list(self.base.rglob('.h3-merge-*')))

    def test_failure_after_install_rolls_back(self):
        real_unlink = m.os.unlink

        def fail_cleanup(path, *args, **kwargs):
            if str(path).startswith('.h3-merge-backup-'):
                raise OSError('injected cleanup failure')
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(m.os, 'unlink', side_effect=fail_cleanup):
            self.assertEqual(self.merge()['skipped'], 2)
        self.assertFalse((self.b / 'common.bin').is_symlink())
        self.assertEqual((self.b / 'common.bin').read_bytes(), self.data)
        self.assertFalse(list(self.base.rglob('.h3-merge-*')))

    def test_source_changes_during_hash_rejected(self):
        original_read = m.os.read
        changed = False

        def read(fd, count):
            nonlocal changed
            block = original_read(fd, count)
            if block and not changed:
                changed = True
                (self.a / 'common.bin').write_bytes(b'x' * len(self.data))
            return block

        with mock.patch.object(m.os, 'read', side_effect=read):
            self.merge()
        self.assertFalse((self.b / 'common.bin').is_symlink())

    def test_source_replaced_before_install_does_not_remove_target(self):
        original_symlink = m.os.symlink
        changed = False

        def symlink(*args, **kwargs):
            nonlocal changed
            value = original_symlink(*args, **kwargs)
            if not changed:
                changed = True
                replacement = self.a / 'replacement'
                replacement.write_bytes(b'x' * len(self.data))
                replacement.replace(self.a / 'common.bin')
            return value

        with mock.patch.object(m.os, 'symlink', side_effect=symlink):
            self.merge()
        self.assertFalse((self.b / 'common.bin').is_symlink())
        self.assertEqual((self.b / 'common.bin').read_bytes(), self.data)
        self.assertFalse(list(self.base.rglob('.h3-merge-*')))

    def test_target_replaced_before_install_keeps_both_versions(self):
        original_symlink = m.os.symlink
        changed = False

        def symlink(*args, **kwargs):
            nonlocal changed
            value = original_symlink(*args, **kwargs)
            if not changed:
                changed = True
                replacement = self.b / 'replacement'
                replacement.write_bytes(b'concurrent writer')
                replacement.replace(self.b / 'common.bin')
            return value

        with mock.patch.object(m.os, 'symlink', side_effect=symlink), contextlib.redirect_stderr(io.StringIO()):
            self.merge()
        self.assertEqual((self.b / 'common.bin').read_bytes(), b'concurrent writer')
        backups = list(self.b.glob('.h3-merge-backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.data)

    def test_hardlinked_pair_already_shared(self):
        (self.b / 'common.bin').unlink()
        os.link(self.a / 'common.bin', self.b / 'common.bin')
        self.assertEqual(self.merge()['already_shared'], 1)

    def test_unvetted_same_named_file_is_untouched(self):
        for folder in (self.a, self.b):
            (folder / 'unknown.bin').write_bytes(self.data)
        self.merge()
        self.assertFalse((self.b / 'unknown.bin').is_symlink())


if __name__ == '__main__':
    unittest.main()
