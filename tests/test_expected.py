"""离线核查文件测试；不实际执行命令行客户端或下载文件。"""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('download_expected', Path(__file__).resolve().parents[1] / 'scripts/download_models.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class ExpectedFileTests(unittest.TestCase):
    def test_matching_size_and_hash(self):
        model = {'expected_files': {'weight': {'bytes': 3, 'sha256': 'abc'}}}
        m.check_expected_files(model, [{'path': 'weight', 'bytes': 3, 'sha256': 'abc'}], True)

    def test_missing_file(self):
        with self.assertRaisesRegex(ValueError, '缺少'):
            m.check_expected_files({'expected_files': {'weight': {'bytes': 3}}}, [], False)

    def test_wrong_size(self):
        with self.assertRaisesRegex(ValueError, '大小'):
            m.check_expected_files({'expected_files': {'weight': {'bytes': 3}}}, [{'path': 'weight', 'bytes': 2}], False)

    def test_wrong_hash(self):
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            m.check_expected_files({'expected_files': {'weight': {'bytes': 3, 'sha256': 'abc'}}}, [{'path': 'weight', 'bytes': 3, 'sha256': 'def'}], True)

    def test_hash_optional(self):
        m.check_expected_files({'expected_files': {'weight': {'bytes': 3, 'sha256': 'abc'}}}, [{'path': 'weight', 'bytes': 3}], False)

    def test_failure_records_incomplete(self):
        for failure in ('empty', 'missing', 'size', 'hash'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cli = root / 'mock-cli'
                cli.write_text('Not executed')
                cli.chmod(0o700)
                catalog = root / 'catalog.json'
                catalog.write_text(json.dumps({'schema_version': 1, 'models': [{'id': 'test', 'repo_id': 'owner/test', 'expected_files': {'weight': {'bytes': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}}}]}))
                def fake_run(args, check):
                    folder = Path(args[args.index('--local-dir') + 1])
                    folder.mkdir(parents=True, exist_ok=True)
                    if failure == 'missing':
                        (folder / 'other').write_bytes(b'abc')
                    elif failure == 'size':
                        (folder / 'weight').write_bytes(b'a')
                    elif failure == 'hash':
                        (folder / 'weight').write_bytes(b'def')
                args = ['--catalog', str(catalog), '--dest', str(root / 'models'), '--select', 'test', '--cli', str(cli), '--yes', '--accept-licenses', '--sha256']
                with mock.patch.object(m.platform, 'system', return_value='Darwin'), mock.patch.object(m.subprocess, 'run', side_effect=fake_run), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(ValueError):
                        m.main(args)
                manifest = json.loads(next((root / 'models/Mac-AI-Manifests').glob('*.json')).read_text())
                self.assertEqual(manifest['status'], 'incomplete')
                self.assertEqual(manifest['models'][-1]['status'], 'incomplete')

if __name__ == '__main__':
    unittest.main()
