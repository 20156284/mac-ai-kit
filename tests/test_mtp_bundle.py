"""MTP 补齐与无损头部适配回归；仅生成极小假权重，不联网、不推理。"""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import download_models as m
import api_inventory as api
import model_inventory as inventory


class MTPBundleTests(unittest.TestCase):
    def fixture(self, root):
        folder = root / 'llm/main/mtp'
        folder.mkdir(parents=True)
        header = {'__metadata__': {'format': 'mlx'}}
        for i in range(15):
            header['fc.weight' if i == 0 else f'layers.0.test{i}.weight'] = {
                'dtype': 'BF16',
                'shape': [1],
                'data_offsets': [i * 2, i * 2 + 2],
            }
        encoded = json.dumps(header).encode()
        payload = bytes(range(30))
        raw = struct.pack('<Q', len(encoded)) + encoded + payload
        (folder / 'model.safetensors').write_bytes(raw)
        model = {
            'id': 'head',
            'repo_id': 'example/head',
            'subdir': 'llm/main/mtp',
            'component': True,
            'strict_download': True,
            'expected_files': {'model.safetensors': {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}},
            'local_adaptations': [
                {
                    'kind': 'safetensors_prefix',
                    'source': 'model.safetensors',
                    'target': 'weights.safetensors',
                    'prefix': 'mtp.',
                    'tensor_count': 15,
                }
            ],
        }
        adapted_header = {('mtp.' + k if k != '__metadata__' else k): v for k, v in header.items()}
        encoded_new = json.dumps(adapted_header, ensure_ascii=False, separators=(',', ':')).encode()
        encoded_new += b' ' * ((-len(encoded_new)) % 8)
        model['generated_expected_files'] = {'weights.safetensors': {'bytes': 8 + len(encoded_new) + len(payload)}}
        return model, folder, header, payload

    def test_final_only_skips_without_source_or_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            model['final_only_adaptation'] = True
            m.adapt_local_weights(model, folder)
            (folder / 'model.safetensors').unlink()
            with (
                mock.patch.object(m, 'verify_file', side_effect=AssertionError('已有最终文件不能全量哈希')),
                mock.patch.object(m.subprocess, 'run') as run,
            ):
                row = m.final_mtp_preflight(model, folder)
                self.assertEqual(row['additional'], 0)
                self.assertTrue(m.download_final_mtp(None, model, folder, 1))
                run.assert_not_called()
            self.assertEqual(api.inspect_model(model, Path(temp))['state'], 'complete')

    def test_final_only_download_cleans_owned_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, payload = self.fixture(Path(temp))
            model['final_only_adaptation'] = True
            raw = (folder / 'model.safetensors').read_bytes()
            (folder / 'model.safetensors').unlink()
            row = m.final_mtp_preflight(model, folder)
            size = model['generated_expected_files']['weights.safetensors']['bytes']
            self.assertEqual(row['additional'], size)
            self.assertEqual(row['peak_additional'], size + len(raw))

            def download(*args, **kwargs):
                (m.staging_directory(model, folder) / 'model.safetensors').write_bytes(raw)

            with mock.patch.object(m.subprocess, 'run', side_effect=download):
                m.download_final_mtp(Path('/fake/client'), model, folder, 1)
            self.assertEqual(
                list(p.name for p in folder.iterdir() if not p.name.endswith('.publish-lock')), ['weights.safetensors']
            )
            self.assertFalse(m.staging_directory(model, folder).exists())
            final = (folder / 'weights.safetensors').read_bytes()
            self.assertEqual(final[-len(payload) :], payload)

    def test_final_only_resumes_verified_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            model['final_only_adaptation'] = True
            stage = m.staging_directory(model, folder, create=True)
            (folder / 'model.safetensors').rename(stage / 'model.safetensors')
            row = m.final_mtp_preflight(model, folder)
            self.assertEqual(row['network'], 0)
            self.assertEqual(row['peak_additional'], row['additional'])
            with mock.patch.object(m.subprocess, 'run') as run:
                m.download_final_mtp(None, model, folder, 1)
                run.assert_not_called()
            self.assertFalse(stage.exists())

    def test_final_only_keeps_preexisting_raw_and_rejects_conflicts(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            model['final_only_adaptation'] = True
            m.download_final_mtp(None, model, folder, 1)
            self.assertTrue((folder / 'model.safetensors').is_file())
            final = folder / 'weights.safetensors'
            final.write_bytes(b'bad')
            with self.assertRaises(ValueError):
                m.final_mtp_preflight(model, folder)
            final.unlink()
            final.symlink_to(folder / 'model.safetensors')
            with self.assertRaises(ValueError):
                m.final_mtp_preflight(model, folder)

    def test_catalog_bundle_is_one_visible_option(self):
        models = m.load_catalog(ROOT / 'catalog.json')
        self.assertEqual(len(m.visible_models(models)), 9)
        main = next(x for x in models if x['id'] == 'qwen38-27b-bf16')
        expanded = m.expand_dependencies([main], models)
        self.assertEqual([x['id'] for x in expanded], ['qwen38-27b-mtp-bf16', main['id']])
        self.assertEqual(m.bundle_size([main], models), '约 55.59 GB')
        self.assertEqual(main['repo_id'], 'mlx-community/Qwen3.8-27B-bf16')
        self.assertEqual(main['subdir'], 'llm/Qwen3.8-27B-bf16')
        self.assertEqual(expanded[0]['generated_expected_files']['weights.safetensors']['bytes'], 849400400)

    def test_header_only_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, header, payload = self.fixture(Path(temp))
            source = (folder / 'model.safetensors').read_bytes()
            result = m.adapt_local_weights(model, folder)
            target = folder / 'weights.safetensors'
            raw = target.read_bytes()
            length = struct.unpack('<Q', raw[:8])[0]
            actual = json.loads(raw[8 : 8 + length])
            self.assertEqual(raw[8 + length :], payload)
            self.assertEqual(actual['__metadata__'], header['__metadata__'])
            for k, v in header.items():
                if k != '__metadata__':
                    self.assertEqual(actual['mtp.' + k], v)
            self.assertEqual(len(actual) - 1, 15)
            self.assertEqual((folder / 'model.safetensors').read_bytes(), source)
            before = target.stat()
            self.assertEqual(m.adapt_local_weights(model, folder), result)
            self.assertEqual(target.stat().st_ino, before.st_ino)
            self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)

    def test_existing_verified_source_never_downloaded(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            with mock.patch.object(m.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
                result = m.download_verified_bundle('unused', model, folder, 2, [], [model])
                run.assert_not_called()
            self.assertEqual(len(result), 1)
            # 已有原主权重亦走同一严格复用路径，不调用下载客户端。
            model['id'] = 'main'
            model.pop('component')
            with mock.patch.object(m.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
                m.download_verified_bundle('unused', model, folder, 2, [], [model])
                run.assert_not_called()

    def test_space_includes_generated_copy_and_rerun_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            plans = [(model, folder, [])]
            estimate = m.reuse_estimate([model], plans, [], [model])
            self.assertGreaterEqual(estimate, model['generated_expected_files']['weights.safetensors']['bytes'])
            m.adapt_local_weights(model, folder)
            self.assertEqual(m.reuse_estimate([model], plans, [], [model]), 0)

    def test_preflight_rejects_invalid_generated_copy_without_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            target = folder / 'weights.safetensors'
            target.write_bytes('用户已有文件'.encode())
            with self.assertRaisesRegex(ValueError, '不覆盖'):
                m.download_preflight([(model, folder, [])], [], [model], base=Path(temp))
            self.assertEqual(target.read_bytes(), '用户已有文件'.encode())

    def test_conflict_and_tamper_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            target = folder / 'weights.safetensors'
            target.write_bytes(b'user content')
            with self.assertRaises(ValueError):
                m.adapt_local_weights(model, folder)
            self.assertEqual(target.read_bytes(), b'user content')
            target.unlink()
            (folder / 'model.safetensors').write_bytes(b'bad')
            with self.assertRaises(ValueError):
                m.adapt_local_weights(model, folder)
            self.assertFalse(target.exists())

    def test_symlink_source_target_and_parent_rejected(self):
        for place in ('source', 'target', 'parent'):
            with self.subTest(place=place), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                model, folder, _, _ = self.fixture(base)
                if place == 'source':
                    source = folder / 'model.safetensors'
                    source.rename(folder / 'real')
                    source.symlink_to('real')
                elif place == 'target':
                    (folder / 'weights.safetensors').symlink_to('model.safetensors')
                else:
                    actual = folder.with_name('actual')
                    folder.rename(actual)
                    folder.symlink_to('actual')
                with self.assertRaises(ValueError):
                    m.adapt_local_weights(model, folder)

    def test_bad_relative_and_precision_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            model, folder, _, _ = self.fixture(Path(temp))
            model['local_adaptations'][0]['target'] = '../outside.safetensors'
            with self.assertRaises(ValueError):
                m.adapt_local_weights(model, folder)
            self.assertFalse((folder.parent / 'outside.safetensors').exists())
            model['local_adaptations'][0]['target'] = 'weights.safetensors'
            raw = (folder / 'model.safetensors').read_bytes().replace(b'BF16', b'FP16')
            (folder / 'model.safetensors').write_bytes(raw)
            model['expected_files']['model.safetensors']['sha256'] = hashlib.sha256(raw).hexdigest()
            with self.assertRaises(ValueError):
                m.adapt_local_weights(model, folder)

    def test_inventory_requires_generated_copy_without_hashing(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            head, folder, _, _ = self.fixture(base)
            main = {
                'id': 'main',
                'repo_id': 'example/main',
                'name': '主模型',
                'subdir': 'llm/main',
                'group': 'llm',
                'requires': ['head'],
                'expected_files': {'main.bin': {'bytes': 1, 'sha256': hashlib.sha256(b'm').hexdigest()}},
            }
            (folder.parent / 'main.bin').write_bytes(b'm')
            with mock.patch.object(m, 'digest_file', side_effect=AssertionError('不得读取权重')):
                self.assertEqual(api.inspect_model(head, base)['state'], 'partial')
                self.assertIn('不完整', inventory.scan(base, [main, head])[1][0]['state'])
            m.adapt_local_weights(head, folder)
            with mock.patch.object(m, 'digest_file', side_effect=AssertionError('不得读取权重')):
                self.assertEqual(api.inspect_model(head, base)['state'], 'complete')
                self.assertEqual(inventory.scan(base, [main, head])[1][0]['state'], '文件齐全')

    def test_api_bf16_runtime_options_documented(self):
        templates = json.loads((ROOT / 'config/api_templates.json').read_text())
        body = templates['models']['qwen38-27b-bf16']['markdown']
        self.assertIn('桌面 App', body)
        self.assertIn('注意力量化', body)
        self.assertIn('尚未在 Mac 实测', body)


if __name__ == '__main__':
    unittest.main()
