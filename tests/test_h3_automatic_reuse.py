"""普通 H3 选择的离线端到端小文件测试，不下载真实权重。"""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('auto_h3_downloader', Path(__file__).resolve().parents[1] / 'scripts/download_models.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class AutomaticH3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = self.root / 'models'
        self.content = {name: {'common.bin': b'shared', 'transformer/model.bin': name.encode(),
                               'config.json': ('config-' + name).encode()} for name in ['FL2VA', 'REF2VA']}
        self.models = [{'id': name, 'repo_id': 'owner/' + name, 'reuse_family': 'h3',
                        'subdir': 'video/' + name, 'group': 'image_video', 'disk_gb': 0.000001,
                        'expected_files': {path: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                                           for path, data in self.content[name].items()}} for name in self.content]
        self.catalog = self.root / 'catalog.json'
        self.catalog.write_text(json.dumps({'schema_version': 1, 'models': self.models}))
        self.cli = self.root / 'ms-hub'
        self.cli.write_text('unused')
        self.cli.chmod(0o700)
        self.fetched = []

    def tearDown(self):
        self.temp.cleanup()

    def install(self, name, folder=None):
        folder = folder or self.base / 'video' / name
        for path, data in self.content[name].items():
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return folder

    def run_download(self, select):
        def download(args, check):
            name = args[2].split('/')[-1]
            folder = Path(args[args.index('--local-dir') + 1])
            includes = [args[i + 1] for i, item in enumerate(args) if item == '--include']
            self.fetched.append((name, includes))
            for path in includes:
                target = folder / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self.content[name][path])
        with mock.patch.object(m.platform, 'system', return_value='Darwin'), mock.patch.object(m.subprocess, 'run', side_effect=download), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(m.main(['--catalog', str(self.catalog), '--models-dir', str(self.base), '--select', select, '--cli', str(self.cli)]), 0)

    def test_real_catalog_metadata_both_directions_and_existing_ref_priority(self):
        catalog = json.loads((Path(__file__).resolve().parents[1] / 'catalog.json').read_text())
        originals = [next(x for x in catalog['models'] if x['id'] == key)
                     for key in ['h3-mlx-8bit', 'h3-ref2va-mlx-8bit']]
        self.assertEqual({x['reuse_family'] for x in originals}, {'minimax-h3-mlx-serve'})
        self.models = [dict(original, expected_files=self.models[i]['expected_files'])
                       for i, original in enumerate(originals)]
        self.catalog.write_text(json.dumps({'schema_version': 1, 'models': self.models}))
        contents = self.content
        self.content = {original['repo_id'].split('/')[-1]: contents[name]
                        for original, name in zip(originals, ['FL2VA', 'REF2VA'])}
        for old_index, new_index, both in [(0, 1, False), (1, 0, False), (1, 0, True)]:
            with self.subTest(old=old_index, both=both):
                self.base = self.root / ('actual-' + str(old_index) + '-' + str(both))
                old, new = originals[old_index], originals[new_index]
                source = self.install(old['repo_id'].split('/')[-1], self.base / old['subdir'])
                self.fetched.clear()
                selected = ','.join(x['id'] for x in originals) if both else new['id']
                with mock.patch.object(m, 'download_verified_bundle', wraps=m.download_verified_bundle) as calls:
                    self.run_download(selected)
                if both:
                    self.assertEqual([c.args[1]['id'] for c in calls.call_args_list], [old['id'], new['id']])
                self.assertEqual(self.fetched, [(new['repo_id'].split('/')[-1], ['transformer/model.bin', 'config.json'])])
                target = self.base / new['subdir']
                self.assertEqual((target / 'common.bin').resolve(), source / 'common.bin')
                self.assertFalse((target / 'transformer/model.bin').is_symlink())

    def test_single_selection_automatically_reuses_other_version_both_directions(self):
        for old, new in [('FL2VA', 'REF2VA'), ('REF2VA', 'FL2VA')]:
            with self.subTest(old=old):
                self.base = self.root / old
                source = self.install(old)
                self.fetched.clear()
                self.run_download(new)
                self.assertEqual(self.fetched, [(new, ['transformer/model.bin', 'config.json'])])
                target = self.base / 'video' / new
                self.assertEqual((target / 'common.bin').resolve(), source / 'common.bin')
                self.assertFalse((target / 'transformer/model.bin').is_symlink())
                self.assertEqual((source / 'transformer/model.bin').read_bytes(), old.encode())

    def test_both_selected_existing_ref_processed_first(self):
        self.install('REF2VA')
        with mock.patch.object(m, 'download_verified_bundle', wraps=m.download_verified_bundle) as calls:
            self.run_download('FL2VA,REF2VA')
        self.assertEqual([c.args[1]['id'] for c in calls.call_args_list], ['REF2VA', 'FL2VA'])
        self.assertEqual(self.fetched, [('FL2VA', ['transformer/model.bin', 'config.json'])])

    def test_both_without_existing_package_download_shared_only_once(self):
        self.run_download('FL2VA,REF2VA')
        self.assertEqual(self.fetched, [('FL2VA', list(self.content['FL2VA'])), ('REF2VA', ['transformer/model.bin', 'config.json'])])

    def test_single_without_existing_package_downloads_normally(self):
        self.run_download('FL2VA')
        self.assertEqual(self.fetched, [('FL2VA', list(self.content['FL2VA']))])

    def test_old_distributed_default_is_automatically_reused(self):
        source = self.install('FL2VA', self.base / 'video/h3-mlx-8bit')
        self.run_download('REF2VA')
        self.assertEqual(self.fetched, [('REF2VA', ['transformer/model.bin', 'config.json'])])
        self.assertTrue((source / 'common.bin').is_file())

    def test_no_external_or_recursive_directory_scan(self):
        self.install('FL2VA', self.root / 'external')
        self.install('FL2VA', self.base / 'nested/FL2VA')
        with mock.patch.object(Path, 'rglob', side_effect=AssertionError('recursive scan forbidden')):
            self.run_download('REF2VA')
        self.assertEqual(self.fetched, [('REF2VA', list(self.content['REF2VA']))])

    def test_automatic_directory_symlink_outside_root_ignored(self):
        outside = self.install('FL2VA', self.root / 'external')
        (self.base / 'video').mkdir(parents=True)
        (self.base / 'video/FL2VA').symlink_to(outside, target_is_directory=True)
        self.run_download('REF2VA')
        self.assertEqual(self.fetched, [('REF2VA', list(self.content['REF2VA']))])

    def test_automatic_file_links_outside_root_broken_or_loop_not_reused(self):
        for kind in ['outside', 'broken', 'loop', 'wrong_hash', 'target']:
            with self.subTest(kind=kind):
                self.base = self.root / kind
                source = self.install('FL2VA')
                path = source / 'common.bin'
                path.unlink()
                outside = self.root / 'external.bin'
                outside.write_bytes(b'shared')
                if kind == 'wrong_hash':
                    path.write_bytes(b'WRONG!')
                elif kind == 'target':
                    path.symlink_to(self.base / 'video/REF2VA/common.bin')
                else:
                    path.symlink_to(outside if kind == 'outside' else ('common.bin' if kind == 'loop' else 'missing'))
                self.fetched.clear()
                self.run_download('REF2VA')
                self.assertEqual(self.fetched, [('REF2VA', list(self.content['REF2VA']))])


if __name__ == '__main__':
    unittest.main()
