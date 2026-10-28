"""整套模型选择、中文分组与简洁预览的离线回归测试。"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('grouped_download', ROOT / 'scripts/download_models.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class GroupedDownloaderTests(unittest.TestCase):
    def setUp(self):
        self.models = m.load_catalog(ROOT / 'catalog.json')
        self.by_id = {entry['id']: entry for entry in self.models}

    def capture(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(m.subprocess, 'run') as run:
            self.assertEqual(m.main(args), 0)
            run.assert_not_called()
        return output.getvalue()

    def test_menu_has_three_groups_without_components(self):
        output = self.capture(['--list'])
        self.assertLess(output.index('\n大模型\n'), output.index('\n音频\n'))
        self.assertLess(output.index('\n音频\n'), output.index('\n图像/视频\n'))
        for key in ('krea2-raw', 'moss-image-base', 'music3-original', 'h3-fl2va-original', 'yue2-original'):
            self.assertNotIn(key, self.by_id)
        for key in ('krea2-text', 'wan21-vae', 'moss-text', 'moss-tokenizer', 'moss-vae'):
            self.assertNotIn(key, output)
        self.assertNotIn('未启用', output)
        self.assertEqual(len(m.visible_models(self.models)), 9)

    def test_uniform_names_expose_format_precision_and_purpose(self):
        for model in m.visible_models(self.models):
            label = model['short_name']
            self.assertIn(model['repo_id'].split('/')[0], label)
            self.assertIn('（', label)
        for name in ('h3-mlx-8bit', 'h3-ref2va-mlx-8bit', 'music3-mlx-8bit', 'qwen38-flash-next-8bit', 'qwen38-27b-bf16', 'yue2-audio-cpp-bf16'):
            self.assertTrue(self.by_id[name]['short_name'].startswith(self.by_id[name]['repo_id']))
            self.assertEqual(Path(self.by_id[name]['subdir']).name, self.by_id[name]['repo_id'].split('/')[-1])
        self.assertNotIn('tts-qwen3-1.7b-orig', self.by_id)

    def test_bundle_size_includes_dependencies_and_deduplicates(self):
        turbo = self.by_id['krea2-turbo']
        self.assertEqual(m.bundle_size([turbo], self.models), '约 35.41 GB')
        self.assertEqual(m.bundle_size([turbo, turbo], self.models), '约 35.41 GB')
        self.assertEqual(m.bundle_size([self.by_id['qwen21-turbo']], self.models), '约 32.44 GB')
        unknown = {'id': 'unknown', 'repo_id': 'o/r'}
        self.assertEqual(m.bundle_size([unknown], [unknown]), '大小未知')

    def test_all_bundle_components_live_within_one_folder(self):
        for key in ('krea2-turbo', 'qwen21-turbo'):
            model = self.by_id[key]
            folder = Path(model['bundle_dir'])
            for component in m.expand_dependencies([model], self.models):
                self.assertTrue(Path(component['subdir']).is_relative_to(folder))

    def test_concise_preview_has_no_file_commands(self):
        output = self.capture(['--select', 'qwen38-flash-next-8bit', '--dry-run'])
        self.assertNotIn('commands', output)
        self.assertNotIn('ngram_table.bin', output)
        self.assertLess(len(output.splitlines()), 10)

    def test_detailed_preview_reuses_shared_components(self):
        output = self.capture(['--select', 'krea2-turbo,krea2-turbo', '--dry-run', '--details'])
        plan = json.loads(output[output.index('{\n'):])
        self.assertEqual(len(plan['commands']), 3)
        paths = [cmd[cmd.index('--local-dir') + 1] for cmd in plan['commands']]
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all('/images/Krea-2' in path for path in paths))

    def test_numeric_selection_matches_grouped_menu(self):
        for index, model in enumerate(m.visible_models(self.models), 1):
            output = self.capture(['--select', str(index), '--dry-run'])
            self.assertIn(model['short_name'], output)

    def test_interactive_root_is_requested_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / '模型目录'
            with mock.patch.object(m.sys.stdin, 'isatty', return_value=True), mock.patch('builtins.input', side_effect=[str(next(i for i, item in enumerate(m.visible_models(self.models), 1) if item['id'] == 'krea2-turbo')), str(target)]) as ask:
                output = self.capture(['--dry-run'])
            self.assertEqual(ask.call_count, 2)
            self.assertEqual(sum('根目录' in call.args[0] for call in ask.call_args_list), 1)
            self.assertIn(str(target / 'images/Krea-2'), output)
            self.assertFalse(target.exists())

    def test_ready_media_full_snapshots_and_explicit_precision(self):
        for key, total in (('music3-mlx-8bit', 13552904390), ('h3-mlx-8bit', 70076153374)):
            entry = self.by_id[key]
            self.assertTrue(entry['enabled'])
            self.assertIn('8bit', entry['short_name'])
            self.assertEqual(entry['runtime'], 'ddalcu/mlx-serve')
            self.assertEqual(sum(f['bytes'] for f in entry['expected_files'].values()), total)
            self.assertEqual(set(entry['include']), set(entry['expected_files']))
            self.assertIn('config.json', entry['expected_files'])
            self.assertIn('LICENSE', entry['expected_files'])
            self.assertTrue(all(len(f['sha256']) == 64 for f in entry['expected_files'].values()))
        self.assertTrue(any('turbo_lora' in name for name in self.by_id['h3-mlx-8bit']['expected_files']))
        self.assertIn('欧盟', self.by_id['h3-mlx-8bit']['license_notice'])

    def test_yue2_high_precision_bundle_has_required_sidecars(self):
        entry = self.by_id['yue2-audio-cpp-bf16']
        self.assertEqual(entry['verified_size_bytes'], 7794596343)
        self.assertEqual(len(entry['include']), 7)
        self.assertIn('yue2-3b-bf16.gguf', entry['include'])
        self.assertIn('yue2-vae-f32.gguf', entry['include'])
        self.assertEqual(sum(path.startswith('sidecars/') for path in entry['include']), 4)
        self.assertFalse(any('q4' in path or 'q8' in path or 'examples/' in path for path in entry['include']))
        self.assertIn('非商业', entry['license_notice'])

    def test_audio_cpp_header_evidence_is_not_runtime_claim(self):
        entries = [entry for entry in self.models if entry.get('runtime') == 'audio.cpp']
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertEqual(entry['readiness'], 'verified_audio_cpp_header_not_mac_tested')
            self.assertIn('尚未在本机', entry['notes'])

    def test_cli_discovery_prefers_path_without_subprocess(self):
        with mock.patch.object(m.shutil, 'which', return_value='/tmp/bin/ms-hub'), mock.patch.object(m.subprocess, 'run') as run:
            self.assertEqual(m.discover_cli(), Path('/tmp/bin/ms-hub'))
            run.assert_not_called()

    def test_cli_discovery_queries_uv_only_when_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / 'ms-hub'
            cli.write_text('测试占位，不执行')
            cli.chmod(0o700)
            with mock.patch.object(m.shutil, 'which', side_effect=lambda name: '/tmp/uv' if name == 'uv' else None), mock.patch.object(m.subprocess, 'run', return_value=mock.Mock(stdout=tmp + '\n')) as run:
                self.assertEqual(m.discover_cli(allow_uv=True), cli)
                run.assert_called_once_with(['uv', 'tool', 'dir', '--bin'], check=True, capture_output=True, text=True)
            with mock.patch.object(m.shutil, 'which', return_value=None), mock.patch.object(m.subprocess, 'run') as run:
                m.discover_cli(allow_uv=False)
                run.assert_not_called()

    def test_bundle_path_rejects_symlink_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'images').symlink_to('/tmp')
            with self.assertRaises(ValueError):
                m.bundle_target(base, self.by_id['krea2-turbo'])
            with self.assertRaises(ValueError):
                m.bundle_target(base, {'bundle_dir': '../escape'})


if __name__ == '__main__':
    unittest.main()
