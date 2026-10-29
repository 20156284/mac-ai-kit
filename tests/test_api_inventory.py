import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('inventory', ROOT / 'scripts/api_inventory.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='api test ')
        self.root = Path(self.temp.name)
        self.catalog = {
            'models': [
                {
                    'id': 'main',
                    'name': '主模型',
                    'subdir': 'audio/main',
                    'runtime': 'audio.cpp',
                    'requires': ['dep'],
                    'expected_files': {'weight.gguf': {'bytes': 4}},
                },
                {'id': 'dep', 'name': '依赖', 'subdir': 'audio/dep', 'expected_files': {'config.json': {'bytes': 2}}},
            ]
        }
        self.templates = {
            'runtimes': [
                {
                    'id': 'audio.cpp',
                    'name': 'Audio',
                    'probes': ['bin/audiocpp_server'],
                    'markdown': '{{BINARY_PATH}} --help',
                }
            ],
            'models': {'main': {'markdown': '模型 {{MODEL_DIR}} 文件 {{MODEL_FILE}} JSON {{MODEL_DIR_JSON}}'}},
        }

    def tearDown(self):
        self.temp.cleanup()

    def put(self, name, data=b'1234'):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def inspect(self):
        return api.inventory(self.root, self.catalog, self.templates)

    def test_complete_size_but_dependency_missing(self):
        self.put('models/audio/main/weight.gguf')
        directory, install, models, tools = self.inspect()
        self.assertEqual(models[0]['state'], 'complete')
        self.assertFalse(models[0]['dependencies_ok'])
        self.assertEqual(models[0]['missing_dependencies'], ['dep'])
        self.assertEqual(tools, [])
        content = api.render(directory, install, models, tools, self.templates)
        self.assertIn('依赖尚未齐全：dep', content)
        self.assertIn('不表示 API 服务正在运行', content)
        self.assertIn("'" + str(self.root / 'models/audio/main') + "'", content)

    def test_partial_and_complete_and_runtime_never_executed(self):
        self.put('audio/main/weight.gguf', b'12')
        self.assertEqual(self.inspect()[2][0]['state'], 'partial')
        self.put('audio/main/weight.gguf')
        self.put('audio/dep/config.json', b'{}')
        binary = self.put('bin/audiocpp_server', b'#!/bin/sh\ntouch NEVER_EXECUTE\n')
        binary.chmod(0o755)
        result = self.inspect()
        self.assertTrue(result[2][0]['dependencies_ok'])
        self.assertEqual(len(result[3]), 1)
        self.assertFalse((self.root / 'NEVER_EXECUTE').exists())

    def test_manual_preservation_idempotence_without_backup(self):
        target = self.put('API.md', '手写说明\n'.encode())
        block = api.render(*self.inspect(), self.templates)
        api.update_document(self.root, block)
        first = target.read_text()
        self.assertTrue(first.startswith('手写说明\n'))
        self.assertEqual(api.update_document(self.root, block), '无变化')
        self.assertEqual(target.read_text(), first)
        self.assertEqual(list(self.root.glob('API.md.bak.*')), [])
        target.write_text(first + '\n末尾手写保留\n')
        self.put('audio/main/weight.gguf')
        api.update_document(self.root, api.render(*self.inspect(), self.templates))
        self.assertTrue(target.read_text().endswith('\n末尾手写保留\n'))
        self.assertEqual(target.read_text().count(api.BEGIN), 1)

    def test_existing_backup_untouched_and_repeated_changes_no_new_backup(self):
        backup = self.put('API.md.bak.existing', b'old backup')
        self.put('API.md', b'handwritten\n')
        for text in ('first', 'second', 'third'):
            api.update_document(self.root, api.BEGIN + '\n' + text + '\n' + api.END + '\n')
        self.assertEqual(list(self.root.glob('API.md.bak.*')), [backup])
        self.assertEqual(backup.read_bytes(), b'old backup')
        self.assertTrue((self.root / 'API.md').read_text().startswith('handwritten\n'))
        self.assertIn('third', (self.root / 'API.md').read_text())

    def test_failed_write_or_replace_preserves_original_and_cleans_temp(self):
        original = b'manual original\r\n'
        target = self.put('API.md', original)
        for operation in ('fsync', 'replace'):
            with self.subTest(operation=operation):
                with patch.object(api.os, operation, side_effect=OSError('simulated failure')):
                    with self.assertRaises(OSError):
                        api.update_document(self.root, api.BEGIN + '\nnew\n' + api.END + '\n')
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(list(self.root.glob('.API.md.*')), [])
                self.assertEqual(list(self.root.glob('API.md.bak.*')), [])

    def test_replace_happens_after_complete_temporary_write(self):
        target = self.put('API.md', b'manual\n')
        block = api.BEGIN + '\nnew\n' + api.END + '\n'
        real_replace = os.replace

        def checked_replace(source, destination):
            self.assertEqual(target.read_bytes(), b'manual\n')
            self.assertTrue(Path(source).read_text().endswith(block))
            self.assertEqual(Path(source).parent, self.root)
            real_replace(source, destination)

        with patch.object(api.os, 'replace', side_effect=checked_replace):
            api.update_document(self.root, block)
        self.assertTrue(target.read_text().endswith(block))

    def test_crlf_and_fifo_preserved(self):
        original = b'# Manual\r\nDo not change.\r\n'
        self.put('API.md', original)
        api.update_document(self.root, api.BEGIN + '\nNew\n' + api.END + '\n')
        self.assertTrue((self.root / 'API.md').read_bytes().startswith(original))
        self.assertEqual(list(self.root.glob('API.md.bak.*')), [])
        (self.root / 'API.md').unlink()
        os.mkfifo(self.root / 'API.md')
        with self.assertRaises(ValueError):
            api.update_document(self.root, 'x')

    def test_symlink_output_and_directory_refused(self):
        other = self.put('outside', b'untouched')
        (self.root / 'API.md').symlink_to(other)
        with self.assertRaises(ValueError):
            api.update_document(self.root, 'bad')
        self.assertEqual(other.read_bytes(), b'untouched')
        (self.root / 'linked').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            api.safe_root(self.root / 'linked')

    def test_directory_symlinks_not_followed(self):
        outside = self.root / 'external'
        (outside / 'main').mkdir(parents=True)
        (outside / 'main/weight.gguf').write_bytes(b'1234')
        (self.root / 'audio').symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.inspect()[2][0]['state'], 'missing')

    def test_h3_leaf_shared_link_metadata(self):
        self.catalog['models'] = [
            {
                'id': 'h3',
                'subdir': 'video/h3',
                'reuse_family': 'minimax-h3-mlx-serve',
                'expected_files': {'text_encoder.safetensors': {'bytes': 4}},
            }
        ]
        source = self.put('other/text_encoder.safetensors')
        destination = self.root / 'video/h3/text_encoder.safetensors'
        destination.parent.mkdir(parents=True)
        destination.symlink_to(source)
        item = self.inspect()[2][0]
        self.assertEqual(item['state'], 'complete')
        self.assertIn('共享链接', ' '.join(item['notes']))
        self.assertIn('未重算', ' '.join(item['notes']))
        source.write_bytes(b'1')
        self.assertEqual(self.inspect()[2][0]['state'], 'partial')

    def test_escape_and_bad_markers_refused(self):
        self.catalog['models'][0]['subdir'] = '../escape'
        with self.assertRaises(ValueError):
            self.inspect()
        self.put('API.md', api.BEGIN.encode())
        with self.assertRaises(ValueError):
            api.update_document(self.root, 'x')

    def test_cli_if_present_and_no_uv(self):
        catalog = self.put('catalog.json', json.dumps(self.catalog).encode())
        templates = self.put('templates.json', json.dumps(self.templates).encode())
        result = subprocess.run(
            [
                os.sys.executable,
                str(ROOT / 'scripts/api_inventory.py'),
                '--directory',
                str(self.root),
                '--catalog',
                str(catalog),
                '--templates',
                str(templates),
                '--if-present',
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'API.md').exists())
        result = subprocess.run(
            ['bash', str(ROOT / 'start.sh'), 'api', '--directory', str(self.root)], capture_output=True, text=True
        )
        # Defaults require the real templates file, supplied with the release.
        if (ROOT / 'config/api_templates.json').exists():
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((self.root / 'API.md').exists())

    def test_hooks_present_preview_guard_and_original_status(self):
        source = (ROOT / 'start.sh').read_text()
        self.assertIn('a) api_menu ;;', source)
        self.assertIn('[ "$install" -eq 1 ] && [ "$result" -eq 0 ]', source)
        self.assertIn('[ "$task" = download ] && [ "$dry" -eq 0 ]', source)
        self.assertIn('return "$result"', source)
        # API dispatch is before ensure_runtime and never invokes it.
        dispatch = source.split('case "$task" in api)', 1)[1].split('remote)', 1)[0]
        self.assertNotIn('ensure_runtime', dispatch)


if __name__ == '__main__':
    unittest.main()
