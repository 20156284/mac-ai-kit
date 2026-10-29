"""模拟不支持硬链接的磁盘；不下载真实权重、不执行推理。"""

import errno
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import test_mtp_bundle
import download_models as m


class ExternalDiskTests(unittest.TestCase):
    fixture = test_mtp_bundle.MTPBundleTests.fixture
    reservation_token = 'Mac AI Kit publication reservation ' + 'a' * 32

    def unstable_reservation_stats(self, target):
        """模拟外置盘同一占位的 fd/path 及连续 lstat 身份不一致。"""
        real_fstat, real_lstat = m.os.fstat, Path.lstat
        calls = {'fstat': 0, 'lstat': 0}

        def different_identity(st, offset):
            values = list(st)
            values[1] = st.st_ino + offset
            values[2] = st.st_dev + offset
            return m.os.stat_result(values)

        def fstat(fd):
            result = real_fstat(fd)
            if not stat.S_ISREG(result.st_mode):
                return result
            calls['fstat'] += 1
            return different_identity(result, 1000000 + calls['fstat'])

        def lstat(path, *args, **kwargs):
            st = real_lstat(path, *args, **kwargs)
            if path == target:
                calls['lstat'] += 1
                return different_identity(st, 2000000 + calls['lstat'])
            return st

        return fstat, lstat

    def test_reported_old_stage_identity_matches_exactly(self):
        model = next(item for item in m.load_catalog(m.ROOT / 'catalog.json') if item['id'] == 'qwen38-27b-mtp-bf16')
        old = Path('/Volumes/Samsung1TB/AI/models /llm/Qwen3.8-27B-bf16/mtp')
        self.assertEqual(
            str(m.staging_identity(model, old)[0]),
            '/Volumes/Samsung1TB/AI/models /llm/Qwen3.8-27B-bf16/.h3-stage-f554d8d2e88dfda927bda0a7',
        )

    def test_mtp_errno45_falls_back_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, payload = self.fixture(Path(temp))
            stage = m.staging_directory(model, folder, create=True)
            (folder / 'model.safetensors').rename(stage / 'model.safetensors')
            with (
                mock.patch.object(m.os, 'link', side_effect=AssertionError('MTP 不得使用硬链接')) as link,
                mock.patch.object(m.subprocess, 'run') as run,
            ):
                m.download_final_mtp(None, model, folder, 1)
                run.assert_not_called()
                link.assert_not_called()
            self.assertEqual((folder / 'weights.safetensors').read_bytes()[-len(payload) :], payload)

    def test_failure_keeps_source_and_tmp_then_reuses(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            with (
                mock.patch.object(m.os, 'link', side_effect=OSError(45, 'unsupported')),
                mock.patch.object(m, 'rename_exclusive', side_effect=PermissionError(errno.EACCES, 'denied')),
            ):
                with self.assertRaises(OSError):
                    m.adapt_local_weights(model, folder)
            copies = list(folder.glob('.*.tmp'))
            self.assertEqual(len(copies), 1)
            self.assertTrue((folder / 'model.safetensors').exists())
            self.assertFalse((folder / 'weights.safetensors').exists())
            with (
                mock.patch.object(m.os, 'link', side_effect=OSError(45, 'unsupported')),
                mock.patch.object(m.shutil, 'copyfileobj', side_effect=AssertionError('不重新生成已验证tmp')),
            ):
                m.adapt_local_weights(model, folder)
            self.assertFalse(copies[0].exists())

    def test_racing_final_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            real = m.rename_exclusive

            def race(src, dst):
                dst.write_bytes(b'user')
                real(src, dst)

            with (
                mock.patch.object(m.os, 'link', side_effect=OSError(45, 'unsupported')),
                mock.patch.object(m, 'rename_exclusive', side_effect=race),
            ):
                with self.assertRaises(FileExistsError):
                    m.adapt_local_weights(model, folder)
            self.assertEqual((folder / 'weights.safetensors').read_bytes(), b'user')
            self.assertEqual(len(list(folder.glob('.*.tmp'))), 1)

    def test_old_whitespace_stage_is_readonly_reusable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model, old, _, _ = self.fixture(root / 'models ')
            stage = m.staging_directory(model, old, create=True)
            (old / 'model.safetensors').rename(stage / 'model.safetensors')
            folder = root / 'models' / model['subdir']
            self.assertEqual(m.final_mtp_preflight(model, folder, old_folder=old)['network'], 0)
            with (
                mock.patch.object(m.os, 'link', side_effect=OSError(45, 'unsupported')),
                mock.patch.object(m.subprocess, 'run') as run,
            ):
                m.download_final_mtp(None, model, folder, 1, old_folder=old)
                run.assert_not_called()
            self.assertTrue((stage / 'model.safetensors').exists())
            self.assertTrue((folder / 'weights.safetensors').exists())

    def test_general_move_fallback_and_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'source', Path(temp) / '.gitattributes'
            source.write_bytes(b'checked')
            inode = source.stat().st_ino
            spec = {'bytes': 7, 'sha256': hashlib.sha256(b'checked').hexdigest()}
            with (
                mock.patch.object(m.os, 'link', side_effect=AssertionError('no link')),
                mock.patch.object(m.shutil, 'copyfileobj', side_effect=AssertionError('no copy')),
                mock.patch.object(m, 'rename_exclusive', side_effect=OSError(45, 'unsupported')),
            ):
                m.publish_verified(source, target, spec, disposable=True)
                self.assertFalse(source.exists())
                self.assertEqual(target.stat().st_ino, inode)
                source.write_bytes(b'checked')
                with self.assertRaises(FileExistsError):
                    m.publish_verified(source, target, spec, disposable=True)
                self.assertEqual(target.read_bytes(), b'checked')
                self.assertTrue(source.exists())

    def test_permission_error_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            with (
                mock.patch.object(m, 'rename_exclusive', side_effect=PermissionError(errno.EACCES, 'denied')),
                mock.patch.object(m.os, 'replace') as rename,
            ):
                with self.assertRaises(PermissionError):
                    m.publish_verified(
                        folder / 'model.safetensors',
                        folder / 'copy.safetensors',
                        model['expected_files']['model.safetensors'],
                        disposable=True,
                    )
                rename.assert_not_called()

    def test_legacy_source_not_moved_or_copied(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            with self.assertRaisesRegex(ValueError, '旧目录文件保持原样'):
                m.publish_verified(
                    folder / 'model.safetensors',
                    folder / 'copy.safetensors',
                    model['expected_files']['model.safetensors'],
                )
            self.assertTrue((folder / 'model.safetensors').exists())
            self.assertFalse((folder / 'copy.safetensors').exists())

    def test_cross_device_never_copies(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'source', Path(temp) / 'target'
            source.write_bytes(b'x')
            spec = {'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}
            with (
                mock.patch.object(m, 'rename_exclusive', side_effect=OSError(errno.EXDEV, 'cross device')),
                mock.patch.object(m.os, 'replace') as replace,
                mock.patch.object(m.shutil, 'copyfileobj') as copy,
            ):
                with self.assertRaises(OSError):
                    m.publish_verified(source, target, spec, disposable=True)
                replace.assert_not_called()
                copy.assert_not_called()
            self.assertEqual(source.read_bytes(), b'x')
            self.assertFalse(target.exists())

    def test_owned_placeholder_resumes_in_bundle_without_network(self):
        import json
        import test_incremental_download as inc

        with tempfile.TemporaryDirectory() as temp:
            model = inc.model()
            folder = Path(temp) / 'Main'
            folder.mkdir()
            stage = m.staging_directory(model, folder, create=True)
            source = stage / 'weights.bin'
            source.write_bytes(b'known model')
            target = folder / 'weights.bin'
            token = self.reservation_token
            target.write_text(token)
            st = target.stat()
            (folder / '.weights.bin.publish-lock').write_text(
                json.dumps({'source': str(source.absolute()), 'identity': [st.st_dev, st.st_ino], 'token': token})
            )
            with (
                mock.patch.object(m.subprocess, 'run') as run,
                mock.patch.object(m, 'rename_exclusive', side_effect=OSError(45, 'unsupported')),
            ):
                m.download_verified_bundle(None, model, folder, 1, [], [model])
                run.assert_not_called()
            self.assertEqual(target.read_bytes(), b'known model')
            self.assertFalse(stage.exists())

    def test_foreign_replacement_before_identity_check_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'source', Path(temp) / 'target'
            source.write_bytes(b'x')
            spec = {'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}
            dump = m.json.dump

            def replace_placeholder(record, stream):
                dump(record, stream)
                other = target.with_name('user-file')
                other.write_bytes(b'user data')
                other.replace(target)

            with (
                mock.patch.object(m, 'rename_exclusive', side_effect=OSError(45, 'unsupported')),
                mock.patch.object(m.json, 'dump', side_effect=replace_placeholder),
            ):
                with self.assertRaises(FileExistsError):
                    m.publish_verified(source, target, spec, disposable=True)
            self.assertEqual(target.read_bytes(), b'user data')
            self.assertTrue(source.exists())

    def test_failed_plain_move_cleans_only_own_placeholder_and_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'source', Path(temp) / 'target'
            source.write_bytes(b'x')
            spec = {'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}
            with (
                mock.patch.object(m, 'rename_exclusive', side_effect=OSError(45, 'unsupported')),
                mock.patch.object(m.os, 'replace', side_effect=PermissionError(errno.EACCES, 'denied')),
            ):
                with self.assertRaises(PermissionError):
                    m.publish_verified(source, target, spec, disposable=True)
            self.assertTrue(source.exists())
            self.assertFalse(target.exists())
            m.publish_verified(source, target, spec, disposable=True)
            self.assertEqual(target.read_bytes(), b'x')

    def test_v32_publish_leftover_moves_without_fetch_and_stage_cleans(self):
        import test_incremental_download as inc

        with tempfile.TemporaryDirectory() as temp:
            model = inc.model()
            folder = Path(temp) / 'Main'
            folder.mkdir()
            stage = m.staging_directory(model, folder, create=True)
            (stage / 'weights.bin').write_bytes(b'known model')
            leftover = folder / ('.weights.bin.' + 'a' * 32 + '.publish')
            leftover.write_bytes(b'known model')
            inode = leftover.stat().st_ino
            unknown = folder / '.weights.bin.unknown.publish'
            unknown.write_bytes(b'user data')
            with (
                mock.patch.object(m.subprocess, 'run') as run,
                mock.patch.object(m.shutil, 'copyfileobj', side_effect=AssertionError('no copy')),
            ):
                m.download_verified_bundle(None, model, folder, 1, [], [model])
                run.assert_not_called()
            self.assertEqual((folder / 'weights.bin').stat().st_ino, inode)
            self.assertFalse(stage.exists())
            self.assertFalse(leftover.exists())
            self.assertEqual(unknown.read_bytes(), b'user data')

    def test_mtp_owned_reservation_is_not_mistaken_for_bad_final(self):
        import json

        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            with mock.patch.object(m, 'publish_verified', side_effect=PermissionError('stop before move')):
                with self.assertRaises(PermissionError):
                    m.adapt_local_weights(model, folder)
            temporary = next(folder.glob('.*.tmp'))
            target = folder / 'weights.safetensors'
            target.write_text(self.reservation_token)
            st = target.stat()
            (folder / '.weights.safetensors.publish-lock').write_text(
                json.dumps(
                    {
                        'source': str(temporary.absolute()),
                        'identity': [st.st_dev, st.st_ino],
                        'token': self.reservation_token,
                    }
                )
            )
            self.assertFalse(m.final_mtp_present(model, folder))
            m.adapt_local_weights(model, folder)
            self.assertFalse(temporary.exists())
            self.assertTrue(m.final_mtp_present(model, folder))

    def test_unstable_fstat_lstat_identity_does_not_block_portable_move(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'source', Path(temp) / 'target'
            payload = b'verified source, not a second copy'
            source.write_bytes(payload)
            inode = source.stat().st_ino
            spec = {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
            fstat, lstat = self.unstable_reservation_stats(target)
            replace = m.os.replace
            with (
                mock.patch.object(m, 'rename_exclusive', side_effect=OSError(45, 'unsupported')),
                mock.patch.object(m.os, 'fstat', side_effect=fstat),
                mock.patch.object(Path, 'lstat', lstat),
                mock.patch.object(m.os, 'link', side_effect=AssertionError('no hard link')),
                mock.patch.object(m.shutil, 'copyfileobj', side_effect=AssertionError('no copy')),
                mock.patch.object(m.os, 'replace', wraps=replace) as move,
            ):
                m.publish_verified(source, target, spec, disposable=True)
                move.assert_called_once_with(source, target)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(target.stat().st_ino, inode)
            self.assertFalse(source.exists())
            self.assertEqual(target.with_name('.target.publish-lock').read_text(), '')

    def test_unstable_identity_failure_cleans_only_own_placeholder(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'source', Path(temp) / 'target'
            source.write_bytes(b'x')
            spec = {'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}
            fstat, lstat = self.unstable_reservation_stats(target)
            with (
                mock.patch.object(m, 'rename_exclusive', side_effect=OSError(45, 'unsupported')),
                mock.patch.object(m.os, 'fstat', side_effect=fstat),
                mock.patch.object(Path, 'lstat', lstat),
                mock.patch.object(m.os, 'replace', side_effect=PermissionError(errno.EACCES, 'denied')),
            ):
                with self.assertRaises(PermissionError):
                    m.publish_verified(source, target, spec, disposable=True)
            self.assertEqual(source.read_bytes(), b'x')
            self.assertFalse(target.exists())
            with mock.patch.object(m, 'rename_exclusive', side_effect=OSError(45, 'unsupported')):
                m.publish_verified(source, target, spec, disposable=True)
            self.assertEqual(target.read_bytes(), b'x')

    def test_interrupted_reservation_recovers_without_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'source', Path(temp) / 'target'
            source.write_bytes(b'checked')
            inode = source.stat().st_ino
            spec = {'bytes': 7, 'sha256': hashlib.sha256(b'checked').hexdigest()}
            dump = m.json.dump

            def interrupt_after_record(record, stream):
                dump(record, stream)
                stream.flush()
                raise KeyboardInterrupt('interrupted after writing reservation')

            with (
                mock.patch.object(m, 'rename_exclusive', side_effect=OSError(45, 'unsupported')),
                mock.patch.object(m.json, 'dump', side_effect=interrupt_after_record),
                mock.patch.object(m.os, 'replace') as move,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    m.publish_verified(source, target, spec, disposable=True)
                move.assert_not_called()
            self.assertEqual(source.read_bytes(), b'checked')
            self.assertTrue(m.owned_publication_reservation(target, source))
            fstat, lstat = self.unstable_reservation_stats(target)
            with (
                mock.patch.object(m, 'rename_exclusive', side_effect=OSError(45, 'unsupported')),
                mock.patch.object(m.os, 'fstat', side_effect=fstat),
                mock.patch.object(Path, 'lstat', lstat),
                mock.patch.object(m.shutil, 'copyfileobj', side_effect=AssertionError('no copy')),
            ):
                m.publish_verified(source, target, spec, disposable=True)
            self.assertFalse(source.exists())
            self.assertEqual(target.read_bytes(), b'checked')
            self.assertEqual(target.stat().st_ino, inode)

    def test_changed_same_size_reservation_and_symlink_are_preserved(self):
        for replacement in ('same_size_content', 'symlink', 'directory'):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as temp:
                source, target = Path(temp) / 'source', Path(temp) / 'target'
                source.write_bytes(b'x')
                spec = {'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}
                victim = Path(temp) / 'user-file'
                dump = m.json.dump

                def change_target(record, stream):
                    dump(record, stream)
                    if replacement == 'same_size_content':
                        target.write_bytes(b'!' * len(record['token'].encode()))
                    elif replacement == 'symlink':
                        victim.write_text(record['token'])
                        target.unlink()
                        target.symlink_to(victim)
                    else:
                        target.unlink()
                        target.mkdir()
                        (target / 'user-file').write_bytes(b'user data')

                with (
                    mock.patch.object(m, 'rename_exclusive', side_effect=OSError(45, 'unsupported')),
                    mock.patch.object(m.json, 'dump', side_effect=change_target),
                    mock.patch.object(m.os, 'replace') as move,
                ):
                    with self.assertRaises(FileExistsError):
                        m.publish_verified(source, target, spec, disposable=True)
                    move.assert_not_called()
                self.assertEqual(source.read_bytes(), b'x')
                if replacement == 'same_size_content':
                    self.assertEqual(target.read_bytes(), b'!' * len(self.reservation_token))
                elif replacement == 'symlink':
                    self.assertTrue(target.is_symlink())
                    self.assertEqual(target.readlink(), victim)
                    self.assertTrue(victim.read_text().startswith('Mac AI Kit publication reservation '))
                else:
                    self.assertEqual((target / 'user-file').read_bytes(), b'user data')

    def test_unowned_record_or_token_never_allows_recovery(self):
        for corruption in (
            'different_source',
            'relative_source',
            'missing_source',
            'short_token',
            'uppercase_token',
            'wrong_prefix',
            'extra_newline',
            'different_token',
            'list_record',
            'null_record',
            'integer_token',
            'malformed_json',
        ):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as temp:
                source, target = Path(temp) / 'source', Path(temp) / 'target'
                source.write_bytes(b'x')
                spec = {'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}
                token = self.reservation_token
                record = {'source': str(source.absolute()), 'identity': [-1, -1], 'token': token}
                content = token
                if corruption == 'different_source':
                    record['source'] = str(source.with_name('different-source').absolute())
                elif corruption == 'relative_source':
                    record['source'] = source.name
                elif corruption == 'missing_source':
                    del record['source']
                elif corruption == 'short_token':
                    record['token'] = content = 'owned'
                elif corruption == 'uppercase_token':
                    record['token'] = content = 'Mac AI Kit publication reservation ' + 'A' * 32
                elif corruption == 'wrong_prefix':
                    record['token'] = content = 'not our reservation ' + 'a' * 32
                elif corruption == 'extra_newline':
                    content += '\n'
                elif corruption == 'different_token':
                    content = 'Mac AI Kit publication reservation ' + 'b' * 32
                elif corruption == 'list_record':
                    record = []
                elif corruption == 'null_record':
                    record = None
                elif corruption == 'integer_token':
                    record['token'] = 123
                target.write_text(content)
                lock = target.with_name('.target.publish-lock')
                lock.write_text('{"source":' if corruption == 'malformed_json' else json.dumps(record))
                before = target.read_bytes()
                self.assertFalse(m.owned_publication_reservation(target, source))
                with mock.patch.object(m, 'rename_exclusive') as rename, mock.patch.object(m.os, 'replace') as move:
                    with self.assertRaises((FileExistsError, ValueError)):
                        m.publish_verified(source, target, spec, disposable=True)
                    rename.assert_not_called()
                    move.assert_not_called()
                self.assertEqual(target.read_bytes(), before)
                self.assertEqual(source.read_bytes(), b'x')

    def test_v33_mtp_wrong_identity_resumes_without_network_or_second_adaptation(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, payload = self.fixture(Path(temp))
            model['final_only_adaptation'] = True
            stage = m.staging_directory(model, folder, create=True)
            (folder / 'model.safetensors').rename(stage / 'model.safetensors')
            with mock.patch.object(m, 'publish_verified', side_effect=PermissionError('stop before move')):
                with self.assertRaises(PermissionError):
                    m.download_final_mtp(None, model, folder, 1)
            temporary = next(folder.glob('.*.tmp'))
            expected, inode = temporary.read_bytes(), temporary.stat().st_ino
            target = folder / 'weights.safetensors'
            target.write_text(self.reservation_token)
            st = target.stat()
            lock = folder / '.weights.safetensors.publish-lock'
            lock.write_text(
                json.dumps(
                    {
                        'source': str(temporary.absolute()),
                        'identity': [st.st_dev + 12345, st.st_ino + 67890],
                        'token': self.reservation_token,
                    }
                )
            )
            reservation_before, lock_before = target.read_bytes(), lock.read_bytes()
            fstat, lstat = self.unstable_reservation_stats(target)
            with (
                mock.patch.object(m.os, 'fstat', side_effect=fstat),
                mock.patch.object(Path, 'lstat', lstat),
                mock.patch.object(m.subprocess, 'run', side_effect=AssertionError('no download')) as download,
                mock.patch.object(
                    m.shutil, 'copyfileobj', side_effect=AssertionError('reuse verified adaptation')
                ) as copy,
                mock.patch.object(m.os, 'link', side_effect=AssertionError('no hard link')) as link,
                mock.patch.object(m, 'rename_exclusive', side_effect=OSError(45, 'unsupported')),
            ):
                self.assertFalse(m.final_mtp_present(model, folder))
                row = m.final_mtp_preflight(model, folder)
                for key in ('network', 'additional', 'generated', 'peak_additional'):
                    self.assertEqual(row[key], 0, key)
                self.assertTrue(row['pending_publish'])
                self.assertEqual(target.read_bytes(), reservation_before)
                self.assertEqual(lock.read_bytes(), lock_before)
                self.assertEqual(temporary.read_bytes(), expected)
                m.download_final_mtp(None, model, folder, 1)
                download.assert_not_called()
                copy.assert_not_called()
                link.assert_not_called()
            self.assertEqual(target.read_bytes(), expected)
            self.assertEqual(target.read_bytes()[-len(payload) :], payload)
            self.assertEqual(target.stat().st_ino, inode)
            self.assertFalse(temporary.exists())
            self.assertFalse(stage.exists())
            self.assertEqual(list(folder.glob('.*.tmp')), [])
            self.assertTrue(m.final_mtp_present(model, folder))
            self.assertEqual(lock.read_text(), '')

    def test_recorded_adaptation_tmp_wins_over_other_valid_tmp(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            with mock.patch.object(m, 'publish_verified', side_effect=PermissionError('stop before move')):
                with self.assertRaises(PermissionError):
                    m.adapt_local_weights(model, folder)
            target = folder / 'weights.safetensors'
            recorded = folder / ('.weights.safetensors.' + 'f' * 32 + '.tmp')
            next(folder.glob('.*.tmp')).rename(recorded)
            expected, inode = recorded.read_bytes(), recorded.stat().st_ino
            unrelated = folder / ('.weights.safetensors.' + '0' * 32 + '.tmp')
            unrelated.write_bytes(expected)
            target.write_text(self.reservation_token)
            lock = folder / '.weights.safetensors.publish-lock'
            lock.write_text(
                json.dumps({'source': str(recorded.absolute()), 'identity': [-1, -1], 'token': self.reservation_token})
            )
            with (
                mock.patch.object(m.subprocess, 'run', side_effect=AssertionError('no download')),
                mock.patch.object(m.shutil, 'copyfileobj', side_effect=AssertionError('no adaptation copy')),
                mock.patch.object(m, 'rename_exclusive', side_effect=OSError(45, 'unsupported')),
            ):
                self.assertTrue(m.final_mtp_preflight(model, folder)['pending_publish'])
                m.download_final_mtp(None, model, folder, 1)
            self.assertFalse(recorded.exists())
            self.assertEqual(target.stat().st_ino, inode)
            self.assertEqual(target.read_bytes(), expected)
            self.assertEqual(unrelated.read_bytes(), expected)
