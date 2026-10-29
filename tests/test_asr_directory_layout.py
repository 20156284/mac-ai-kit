"""ASR 新旧目录兼容；只用微型离线文件，不下载真实模型。"""

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import api_inventory as api
import download_models as downloader
import model_inventory as inventory


class AsrDirectoryLayout(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        catalog = json.loads((ROOT / 'catalog.json').read_text())
        self.model = copy.deepcopy(next(m for m in catalog['models'] if m['id'] == 'asr-qwen3-1.7b-f16'))
        self.name = next(iter(self.model['expected_files']))
        self.data = b'ASR test fixture'
        self.model['expected_files'][self.name] = {
            'bytes': len(self.data),
            'sha256': hashlib.sha256(self.data).hexdigest(),
        }
        self.folder = self.base / self.model['subdir']

    def write(self, old=False, data=None):
        folder = self.base / (self.model['legacy_subdirs'][0] if old else self.model['subdir'])
        path = folder / self.name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.data if data is None else data)
        return path

    def test_new_target_preserves_upstream_path(self):
        self.assertEqual(self.folder / self.name, self.base / 'audio/Qwen3-ASR-1.7B-GGUF/qwen3-asr-1.7b-f16.gguf')
        self.assertEqual(downloader.bundle_target(self.base, self.model), self.base / 'audio/Qwen3-ASR-1.7B-GGUF')
        self.assertEqual(self.model['repo_id'], 'HereIsMark/audio.cpp-gguf')
        self.assertIn(self.name, self.model['include'])

    def test_unrelated_audio_folder_not_reported_as_asr(self):
        (self.base / 'audio/other-model').mkdir(parents=True)
        self.assertEqual(inventory.scan(self.base, [self.model])[1], [])
        self.assertEqual(api.inspect_model(self.model, self.base)['state'], 'missing')

    def test_new_and_old_readonly_inventory_and_api_paths(self):
        for old in (False, True):
            with self.subTest(old=old):
                path = self.write(old)
                with patch.object(Path, 'open', side_effect=AssertionError('只读元数据，不打开权重')):
                    item = api.inspect_model(self.model, self.base)
                    self.assertEqual(item['state'], 'complete')
                    self.assertEqual(item['path'] / self.name, path)
                    self.assertEqual(item['display_path'], path.parent)
                    self.assertEqual(inventory.scan(self.base, [self.model])[1][0]['state'], '文件齐全')
                    templates = {'models': {self.model['id']: {'markdown': '目录 {{MODEL_DIR}} 文件 {{MODEL_FILE}}'}}}
                    item.update(missing_dependencies=[], dependencies_ok=True)
                    rendered = api.render(self.base, self.base, [item], [], templates)
                    self.assertIn('- 目录：' + str(path.parent), rendered)
                    self.assertIn('文件 ' + str(path), rendered)
                    self.assertIn('目录 ' + str(path.parent), rendered)
                path.unlink()

    def test_complete_legacy_beats_partial_new(self):
        old = self.write(True)
        self.write(False, b'x')
        self.assertEqual(api.inspect_model(self.model, self.base)['path'] / self.name, old)
        self.assertEqual(inventory.scan(self.base, [self.model])[1][0]['state'], '文件齐全')

    def test_new_preferred_if_both_complete(self):
        self.write(True)
        new = self.write()
        self.assertEqual(api.inspect_model(self.model, self.base)['path'] / self.name, new)

    def test_legacy_reused_without_download_move_or_delete(self):
        old = self.write(True)
        plans = [(self.model, self.folder, [])]
        row = downloader.download_preflight(plans, [], [self.model], base=self.base)[0]
        self.assertEqual(row['network'], 0)
        self.assertEqual(row['local_reuse'][self.name], old)
        with (
            patch.object(downloader.subprocess, 'run', side_effect=AssertionError('不应调用下载客户端')),
            self.assertRaisesRegex(ValueError, '旧目录文件保持原样'),
        ):
            downloader.download_verified_bundle(
                Path('/no-client'), self.model, self.folder, 1, [], [self.model], row['local_reuse']
            )
        new = self.folder / self.name
        self.assertTrue(old.is_file())
        self.assertFalse(new.exists())
        self.assertEqual(old.read_bytes(), self.data)

    def test_mock_download_installs_only_new_layout(self):
        def fake_download(args, **kwargs):
            stage = Path(args[args.index('--local-dir') + 1])
            self.assertEqual(args[args.index('--include') + 1], self.name)
            p = stage / self.name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(self.data)

        with patch.object(downloader.subprocess, 'run', side_effect=fake_download):
            downloader.download_verified_bundle(Path('/fake-client'), self.model, self.folder, 1, [], [self.model])
        self.assertTrue((self.folder / self.name).is_file())
        self.assertFalse((self.base / 'audio/audio.cpp-gguf').exists())

    def test_legacy_directory_symlink_not_followed(self):
        outside = self.base / 'outside'
        p = outside / self.name
        p.parent.mkdir(parents=True)
        p.write_bytes(self.data)
        self.folder.mkdir()
        (self.folder / 'audio.cpp-gguf').symlink_to(outside, target_is_directory=True)
        self.assertEqual(api.inspect_model(self.model, self.base)['state'], 'missing')
        self.assertEqual(inventory.scan(self.base, [self.model])[1], [])
        sources = downloader.automatic_model_sources(self.base, self.model)
        self.assertNotIn(self.folder / 'audio.cpp-gguf', sources)


if __name__ == '__main__':
    unittest.main()
