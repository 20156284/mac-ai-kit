"""可选 Qwen 条目的离线回归测试；不获取权重、不调用命令行客户端。"""

import contextlib
import fnmatch
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('download_qwen', ROOT / 'scripts/download_models.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
IDS = ('qwen38-flash-next-8bit', 'qwen38-27b-bf16')


class QwenOptionsTests(unittest.TestCase):
    def setUp(self):
        self.models = m.load_catalog(ROOT / 'catalog.json')
        self.by_id = {entry['id']: entry for entry in self.models}

    def test_verified_file_snapshots(self):
        for key, total in zip(IDS, (194900880527, 54743807404)):
            entry = self.by_id[key]
            self.assertTrue(entry['enabled'])
            self.assertEqual(entry['requires'], ['qwen38-27b-mtp-bf16'] if key == IDS[1] else [])
            self.assertEqual(len(Path(entry['subdir']).parts), 2)
            self.assertEqual(Path(entry['subdir']).parts[0], 'llm')
            self.assertTrue(entry['verification']['revision_is_mutable'])
            self.assertEqual(sum(x['bytes'] for x in entry['expected_files'].values()), total)
            for name, audited in entry['expected_files'].items():
                self.assertTrue(any(fnmatch.fnmatchcase(name, p) for p in entry['include']))
                self.assertRegex(audited['sha256'], r'^[0-9a-f]{64}$')
            self.assertIn('config.json', entry['expected_files'])
            self.assertIn('model.safetensors.index.json', entry['expected_files'])
        flash = self.by_id[IDS[0]]
        self.assertEqual(flash['expected_files']['ngram_table.bin']['bytes'], 57600276856)
        self.assertEqual(sum(p.endswith('.safetensors') for p in flash['expected_files']), 101)
        bf16 = self.by_id[IDS[1]]
        self.assertNotIn('mtp.safetensors', bf16['expected_files'])
        self.assertIn('+ MTP', bf16['name'])
        self.assertEqual(sum(p.endswith('.safetensors') for p in bf16['expected_files']), 11)

    def test_list_is_read_only(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(m.subprocess, 'run') as run:
            self.assertEqual(m.main(['--list', '--details']), 0)
            run.assert_not_called()
        for key in IDS:
            self.assertIn(key, output.getvalue())

    def test_selected_dry_run_is_read_only_and_selective(self):
        for keys in ((IDS[0],), (IDS[1],), IDS):
            with self.subTest(keys=keys), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / 'Shared models'
                output = io.StringIO()
                with contextlib.redirect_stdout(output), mock.patch.object(m.subprocess, 'run') as run:
                    self.assertEqual(
                        m.main(['--select', ','.join(keys), '--models-dir', str(target), '--dry-run', '--details']), 0
                    )
                    run.assert_not_called()
                plan = json.loads(output.getvalue()[output.getvalue().index('{\n') :])
                expanded = m.expand_dependencies([self.by_id[key] for key in keys], self.models)
                self.assertEqual(len(plan['commands']), len(expanded))
                for command, entry in zip(plan['commands'], expanded):
                    key = entry['id']
                    self.assertEqual(command[2], self.by_id[key]['repo_id'])
                    self.assertEqual(command[command.index('--local-dir') + 1], str(target / self.by_id[key]['subdir']))
                    self.assertNotIn('--drafter', command)
                self.assertFalse(target.exists())

    def test_no_implicit_qwen_dependencies_or_default_download(self):
        for entry in self.models:
            self.assertFalse(set(entry.get('requires', [])) & set(IDS))
        with (
            mock.patch.object(m.sys.stdin, 'isatty', return_value=False),
            mock.patch.object(m.subprocess, 'run') as run,
        ):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                m.main([])
            self.assertEqual(raised.exception.code, 2)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
