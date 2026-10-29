"""已下载模型只读清单与去重建议；使用微型临时文件，不读取真实权重。"""

import contextlib
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import model_inventory as inventory


class ModelInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve() / 'models'
        self.base.mkdir()
        self.data = b'tiny fixture'
        spec = {'bytes': len(self.data), 'sha256': hashlib.sha256(self.data).hexdigest()}
        self.models = [
            {
                'id': key,
                'repo_id': 'test/' + variant,
                'name': variant,
                'subdir': 'video/' + variant,
                'group': 'image_video',
                'expected_files': {'shared.bin': dict(spec)},
            }
            for key, variant in zip(inventory.IDS, ('FL', 'REF'))
        ]

    def write(self, index, data=None):
        folder = self.base / self.models[index]['subdir']
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'shared.bin'
        path.write_bytes(self.data if data is None else data)
        return path

    def test_empty_directory_is_not_model(self):
        self.assertEqual(inventory.scan(self.base, self.models)[1:], ([], 0, 0))

    def test_partial_model_does_not_claim_complete(self):
        self.write(0, b'x')
        _, rows, size, count = inventory.scan(self.base, self.models)
        self.assertEqual(rows[0]['state'], '不完整 0/1')
        self.assertEqual(rows[0]['bytes'], 1)
        self.assertEqual((size, count), (0, 0))

    def test_expected_metadata_no_weight_read(self):
        self.write(0)
        self.write(1)
        with patch.object(Path, 'open', side_effect=AssertionError('不可打开权重')):
            _, rows, size, count = inventory.scan(self.base, self.models)
        self.assertEqual([r['state'] for r in rows], ['文件齐全'] * 2)
        self.assertEqual((size, count), (len(self.data), 1))

    def test_shared_symlink_not_suggested_again(self):
        a, b = self.write(0), self.write(1)
        b.unlink()
        b.symlink_to(os.path.relpath(a, b.parent))
        _, rows, size, count = inventory.scan(self.base, self.models)
        self.assertEqual(rows[1]['state'], '文件齐全')
        self.assertEqual(rows[1]['shared'], 1)
        self.assertEqual((size, count), (0, 0))

    def test_shared_hardlink_not_suggested_again(self):
        a, b = self.write(0), self.write(1)
        b.unlink()
        os.link(a, b)
        self.assertEqual(inventory.scan(self.base, self.models)[2:], (0, 0))

    def test_external_symlink_not_followed(self):
        p = self.write(0)
        outside = self.base.parent / 'outside'
        outside.write_bytes(self.data)
        p.unlink()
        p.symlink_to(outside)
        _, rows, _, _ = inventory.scan(self.base, self.models)
        self.assertEqual(rows[0]['bytes'], 0)
        self.assertEqual(rows[0]['state'], '不完整 0/1')

    def test_directory_link_not_followed(self):
        self.write(0)
        folder = self.base / self.models[1]['subdir']
        folder.symlink_to(self.base / self.models[0]['subdir'], target_is_directory=True)
        _, rows, size, count = inventory.scan(self.base, self.models)
        self.assertEqual(len(rows), 1)
        self.assertEqual((size, count), (0, 0))

    def test_dependency_files_fold_into_one_row(self):
        a = self.models[0]
        a['requires'] = ['encoder']
        component = dict(self.models[1], id='encoder', component=True)
        self.models.append(component)
        self.write(0)
        _, rows, _, _ = inventory.scan(self.base, self.models)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state'], '不完整 1/2')

    def test_report_is_explicit_estimate_not_disk_free_claim(self):
        self.write(0)
        self.write(1)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            inventory.display(*inventory.scan(self.base, self.models))
        self.assertIn('疑似重复', output.getvalue())
        self.assertIn('非实际磁盘释放量', output.getvalue())
        self.assertIn('未验证推理', output.getvalue())

    def test_entry_read_only_and_nonexistent_root(self):
        missing = self.base / 'not-created'
        env = dict(os.environ, HOME=str(self.base.parent / 'home'))
        result = subprocess.run(
            ['/bin/bash', str(ROOT / 'start.sh'), 'installed-models', '--models-dir', str(missing)],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('目录尚不存在', result.stdout)
        self.assertFalse(missing.exists())
        self.assertFalse((self.base.parent / 'home').exists())

    def test_legacy_directory_is_visible(self):
        p = self.base / inventory.LEGACY / 'shared.bin'
        p.parent.mkdir(parents=True)
        p.write_bytes(self.data)
        _, rows, _, _ = inventory.scan(self.base, self.models)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state'], '文件齐全')

    def test_empty_model_folder_marked_incomplete(self):
        (self.base / self.models[0]['subdir']).mkdir(parents=True)
        rows = inventory.scan(self.base, self.models)[1]
        self.assertEqual(rows[0]['state'], '不完整 0/1')


if __name__ == '__main__':
    unittest.main()
