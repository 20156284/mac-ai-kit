"""模型发现与严格分词的全离线测试；不访问服务，不执行第三方实现。"""

import contextlib
import asyncio
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


discovery = load('tested_discovery', 'benchmark_discovery.py')
runner = load('tested_runner', 'benchmark_runner.py')


class DiscoveryTests(unittest.TestCase):
    def fetch(self, value=None, raw=None):
        response = Mock()
        response.read.return_value = raw if raw is not None else json.dumps(value).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        return opener, response

    def test_discovery_request_is_bounded_and_credential_free(self):
        opener, response = self.fetch({'data': [{'id': 'owner/model'}]})
        with patch.object(discovery, 'build_opener', return_value=opener) as factory:
            rows = discovery.discover('http://127.0.0.1:8000/v1/')
        self.assertEqual(rows, [{'id': 'owner/model'}])
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:8000/v1/models')
        self.assertEqual(request.header_items(), [('Accept', 'application/json')])
        self.assertEqual(opener.open.call_args.kwargs, {'timeout': 8})
        self.assertEqual(factory.call_args.args[0].proxies, {})
        self.assertIsInstance(factory.call_args.args[1], discovery.NoRedirect)
        response.read.assert_called_once_with(discovery.LIMIT + 1)

    def test_transport_failures_propagate_once(self):
        errors = [
            TimeoutError('timeout'),
            URLError('offline'),
            HTTPError('http://local/v1/models', 401, 'unauthorized', {}, None),
            HTTPError('http://local/v1/models', 503, 'loading', {}, None),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                opener = Mock()
                opener.open.side_effect = error
                with patch.object(discovery, 'build_opener', return_value=opener):
                    with self.assertRaises(type(error)):
                        discovery.discover('http://local/v1')
                opener.open.assert_called_once()

    def test_invalid_payloads_and_duplicate_ids(self):
        payloads = [
            None,
            [],
            {},
            {'data': []},
            {'data': 'bad'},
            {'data': [None]},
            {'data': [{}]},
            {'data': [{'id': 1}]},
            {'data': [{'id': ''}]},
            {'data': [{'id': '--help'}]},
            {'data': [{'id': 'bad\x1b'}]},
            {'data': [{'id': 'bad\x7f'}]},
            {'data': [{'id': 'same'}, {'id': 'same'}]},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                opener, _ = self.fetch(payload)
                with patch.object(discovery, 'build_opener', return_value=opener):
                    with self.assertRaises(ValueError):
                        discovery.discover('http://local/v1')
        for raw in (b'not json', b'x' * (discovery.LIMIT + 1)):
            opener, _ = self.fetch(raw=raw)
            with patch.object(discovery, 'build_opener', return_value=opener):
                with self.assertRaises(ValueError):
                    discovery.discover('http://local/v1')

    def test_redirect_is_rejected(self):
        with self.assertRaises(ValueError):
            discovery.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://other')

    def test_selection_single_multi_invalid_and_cancel(self):
        rows = [{'id': 'one'}, {'id': 'two'}]
        with patch('builtins.input') as prompt:
            self.assertEqual(discovery.choose(rows[:1], 'choose', str), rows[0])
            prompt.assert_not_called()
        with patch('builtins.input', return_value='2'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(discovery.choose(rows, 'choose', str), rows[1])
        for value, error in [
            ('', EOFError),
            ('q', EOFError),
            ('0', ValueError),
            ('3', ValueError),
            ('bad', ValueError),
        ]:
            with patch('builtins.input', return_value=value) as prompt, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(error):
                    discovery.choose(rows, 'choose', str)
                prompt.assert_called_once()


class LocalResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.kit = self.root / 'kit'
        self.base = self.root / 'models'
        self.base.mkdir()
        (self.kit / 'config').mkdir(parents=True)
        (self.kit / 'catalog.json').write_text('{"models": []}')
        (self.kit / 'config/benchmark_tokenizers.json').write_text('{"models": []}')
        self.patcher = patch.object(discovery, 'ROOT', self.kit)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def local(self, name, repo=None, tokenizer=True):
        folder = self.base / name
        folder.mkdir(parents=True)
        (folder / 'config.json').write_text(json.dumps({'_name_or_path': repo} if repo else {}))
        if tokenizer:
            (folder / 'tokenizer.json').write_text('{"version":"1.0"}')
        return folder

    def test_repo_basename_and_root_exact_matches(self):
        folder = self.local('llm/Model-8bit', 'original/Model')
        for model in (
            {'id': 'original/Model'},
            {'id': 'Model-8bit'},
            {'id': 'alias', 'root': 'original/Model'},
            {'id': str(folder)},
        ):
            with self.subTest(model=model), patch('builtins.input') as prompt:
                resolved = discovery.resolve_tokenizer(model, self.base)
                self.assertEqual(resolved['kind'], 'local')
                self.assertEqual(resolved['value'], str(folder))
                self.assertTrue(resolved['evidence'])
                prompt.assert_not_called()

    def test_relative_model_path_uses_existing_tokenizer_without_hub(self):
        folder = self.local('llm/Qwen3.8-27B-bf16')
        for model in (
            {'id': 'llm/Qwen3.8-27B-bf16'},
            {'id': './llm/Qwen3.8-27B-bf16'},
            {'id': 'alias', 'root': 'llm/Qwen3.8-27B-bf16'},
        ):
            with patch('builtins.input') as prompt:
                result = discovery.resolve_tokenizer(model, self.base)
            self.assertEqual(result['kind'], 'local')
            self.assertEqual(result['value'], str(folder))
            prompt.assert_not_called()

    def test_known_missing_folder_falls_back_to_catalog_repo_not_llm_path(self):
        (self.kit / 'catalog.json').write_text(
            json.dumps(
                {
                    'models': [
                        {
                            'id': 'qwen',
                            'repo_id': 'mlx-community/Qwen3.8-27B-bf16',
                            'subdir': 'llm/Qwen3.8-27B-bf16',
                            'group': 'llm',
                        }
                    ]
                }
            )
        )
        result = discovery.resolve_tokenizer({'id': 'llm/Qwen3.8-27B-bf16'}, self.base)
        self.assertEqual(result['kind'], 'public')
        self.assertEqual(result['value'], 'mlx-community/Qwen3.8-27B-bf16')

    def test_unknown_local_path_never_becomes_public_repo_or_unrelated_alias(self):
        self.local('llm/Unrelated')
        for name in ('llm/Missing', './llm/Missing', '/Volumes/Disk/Missing', '../Missing'):
            with patch('builtins.input') as prompt:
                with self.assertRaisesRegex(ValueError, '不会把本地路径'):
                    discovery.resolve_tokenizer({'id': name}, self.base)
                prompt.assert_not_called()

    def test_local_metadata_relative_path_never_becomes_public_source(self):
        self.local('llm/MissingTokenizer', 'llm/MissingTokenizer', tokenizer=False)
        with self.assertRaises(ValueError):
            discovery.resolve_tokenizer({'id': 'llm/MissingTokenizer'}, self.base)

    def test_standalone_entry_reads_saved_external_model_root(self):
        saved = self.root / 'settings/mac-ai-kit/model-directory'
        saved.parent.mkdir(parents=True)
        saved.write_text(str(self.base) + '\n')
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(saved.parent.parent)}):
            self.assertEqual(discovery.default_model_root(), self.base)
            folder = self.local('llm/Qwen3.8-27B-bf16')
            result = discovery.resolve_tokenizer({'id': 'llm/Qwen3.8-27B-bf16'}, discovery.default_model_root())
            self.assertEqual(result['value'], str(folder))
            saved.write_text('relative/models\n')
            with self.assertRaises(ValueError):
                discovery.default_model_root()

    def test_catalog_and_auditable_mapping(self):
        folder = self.local('llm/Quantized')
        (self.kit / 'catalog.json').write_text(
            json.dumps(
                {
                    'models': [
                        {'id': 'kit-model', 'repo_id': 'converter/Quantized', 'subdir': 'llm/Quantized', 'group': 'llm'}
                    ]
                }
            )
        )
        (self.kit / 'config/benchmark_tokenizers.json').write_text(
            json.dumps(
                {
                    'models': [
                        {
                            'repo_id': 'converter/Quantized',
                            'base_model': 'original/Base',
                            'source': 'https://example.org/model-card',
                            'tokenizer_sha256': hashlib.sha256((folder / 'tokenizer.json').read_bytes()).hexdigest(),
                        }
                    ]
                }
            )
        )
        for identity in ('kit-model', 'converter/Quantized', 'original/Base'):
            result = discovery.resolve_tokenizer({'id': identity}, self.base)
            self.assertEqual(result['value'], str(folder))
            self.assertIn('https://example.org/model-card', result['evidence'])

    def test_ambiguous_basename_requires_selection(self):
        self.local('a/Model')
        self.local('b/Model')
        second = discovery.local_candidates(self.base)[1]['path']
        with patch('builtins.input', return_value='2') as prompt, contextlib.redirect_stdout(io.StringIO()):
            result = discovery.resolve_tokenizer({'id': 'Model'}, self.base)
        self.assertEqual(result['value'], str(second))
        prompt.assert_called_once()

    def test_single_unmatched_alias_requires_explicit_confirmation(self):
        folder = self.local('llm/Local')
        with patch('builtins.input', return_value='1') as prompt, contextlib.redirect_stdout(io.StringIO()):
            result = discovery.resolve_tokenizer({'id': 'custom-alias'}, self.base)
        prompt.assert_called_once()
        self.assertEqual(result['value'], str(folder))
        self.assertIn('用户明确选择', result['evidence'][0])
        for reply, error in [('q', EOFError), ('', EOFError), ('9', ValueError)]:
            with patch('builtins.input', return_value=reply) as prompt, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(error):
                    discovery.resolve_tokenizer({'id': 'custom-alias'}, self.base)
                prompt.assert_called_once()

    def test_public_uses_exact_repo_never_guesses_quantization_suffix(self):
        self.local('llm/Unrelated', 'other/model')
        for model in ({'id': 'owner/Model-Q4'}, {'id': 'alias', 'root': 'owner/Model-Q4'}):
            with patch('builtins.input') as prompt:
                result = discovery.resolve_tokenizer(model, self.base)
            self.assertEqual(result['kind'], 'public')
            self.assertEqual(result['value'], 'owner/Model-Q4')
            prompt.assert_not_called()
        with self.assertRaises(ValueError):
            discovery.resolve_tokenizer({'id': 'owner/one', 'root': 'owner/two'}, self.base)

    def test_no_candidates_exits_without_prompt(self):
        self.local('llm/NoTokenizer', tokenizer=False)
        with patch('builtins.input') as prompt:
            with self.assertRaises(ValueError):
                discovery.resolve_tokenizer({'id': 'alias'}, self.base)
            prompt.assert_not_called()

    def test_missing_tokenizer_uses_exact_local_metadata_repo(self):
        self.local('llm/Converted', 'owner/Exact', tokenizer=False)
        result = discovery.resolve_tokenizer({'id': 'Converted'}, self.base)
        self.assertEqual(result['kind'], 'public')
        self.assertEqual(result['value'], 'owner/Exact')
        self.assertTrue(any('_name_or_path=owner/Exact' in x for x in result['evidence']))

    def test_conflicting_metadata_is_not_silently_guessed(self):
        folder = self.local('llm/Conflicting', 'owner/One', tokenizer=False)
        (folder / 'tokenizer_config.json').write_text('{"_name_or_path":"owner/Two"}')
        with self.assertRaises(ValueError):
            discovery.resolve_tokenizer({'id': 'Conflicting'}, self.base)

    def test_cross_repo_mapping_requires_exact_tokenizer_hash(self):
        folder = self.local('llm/Quantized')
        (self.kit / 'catalog.json').write_text(
            json.dumps(
                {
                    'models': [
                        {'id': 'kit-model', 'repo_id': 'converter/Quantized', 'subdir': 'llm/Quantized', 'group': 'llm'}
                    ]
                }
            )
        )
        (self.kit / 'config/benchmark_tokenizers.json').write_text(
            json.dumps(
                {
                    'models': [
                        {
                            'repo_id': 'converter/Quantized',
                            'base_model': 'original/Base',
                            'source': 'https://example.org/model-card',
                            'tokenizer_sha256': '0' * 64,
                        }
                    ]
                }
            )
        )
        result = discovery.resolve_tokenizer({'id': 'original/Base'}, self.base)
        self.assertEqual(result['kind'], 'public')
        self.assertEqual(result['value'], 'original/Base')

    def test_symlinks_and_nonlanguage_components_are_not_candidates(self):
        real = self.local('actual')
        (self.base / 'linked').symlink_to(real, target_is_directory=True)
        broken = self.local('linked-tokenizer', tokenizer=False)
        (broken / 'tokenizer.json').symlink_to(real / 'tokenizer.json')
        clip = self.local('clip')
        (clip / 'config.json').write_text('{"model_type": "clip"}')
        candidates = discovery.local_candidates(self.base)
        self.assertEqual([x['path'] for x in candidates], [str(real)])
        with self.assertRaises(ValueError):
            discovery.local_candidates(self.base / 'linked')


class StrictTokenizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'tokenizer.json'
        self.source.write_text('{"version":"1.0"}')
        self.tokenizer = Mock()
        self.tokenizer.encode.return_value = types.SimpleNamespace(ids=[1, 2])
        self.loader = Mock(return_value=self.tokenizer)
        self.download = Mock(return_value=str(self.source))
        self.modules = {
            'tokenizers': types.SimpleNamespace(Tokenizer=types.SimpleNamespace(from_file=self.loader)),
            'llama_benchy.corpus': types.SimpleNamespace(LightweightTokenizer=lambda value: value),
            'huggingface_hub': types.SimpleNamespace(hf_hub_download=self.download),
        }
        self.old_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.old_cwd)

    def test_local_strict_load_disables_truncation_padding_without_hub(self):
        with patch.dict(sys.modules, self.modules):
            self.assertIs(runner.strict_tokenizer(str(self.root)), self.tokenizer)
        self.loader.assert_called_once_with(str(self.source))
        self.tokenizer.no_truncation.assert_called_once()
        self.tokenizer.no_padding.assert_called_once()
        self.download.assert_not_called()
        record = json.loads((self.root / 'tokenizer.json.audit').read_text())
        self.assertFalse(record['fallback'])
        self.assertFalse(record['truncation'])
        self.assertFalse(record['padding'])
        self.assertEqual(len(record['sha256']), 64)

    def test_public_only_downloads_tokenizer_without_auth(self):
        with patch.dict(sys.modules, self.modules):
            runner.strict_tokenizer('owner/exact-model')
        self.download.assert_called_once_with(repo_id='owner/exact-model', filename='tokenizer.json', token=False)

    def test_load_error_or_empty_encoding_never_falls_back(self):
        self.loader.side_effect = ValueError('malformed tokenizer')
        with patch.dict(sys.modules, self.modules):
            with self.assertRaises(ValueError):
                runner.strict_tokenizer(str(self.source))
        self.download.assert_not_called()
        self.loader.assert_called_once_with(str(self.source))
        self.loader.side_effect = None
        self.tokenizer.encode.return_value = types.SimpleNamespace(ids=[])
        with patch.dict(sys.modules, self.modules):
            with self.assertRaises(ValueError):
                runner.strict_tokenizer(str(self.source))
        self.assertFalse((self.root / 'tokenizer.json.audit').exists())

    def test_prompt_count_guard_rejects_large_mismatch_only(self):
        tokenizer = types.SimpleNamespace(encode=lambda text, **kwargs: list(text))
        client = types.SimpleNamespace(_non_empty_user_content=lambda text: text or 'empty')
        for reported, expected_error in ((2000, False), (2100, False), (50, True), (4000, True), (0, False)):
            result = types.SimpleNamespace(error=None, prompt_tokens=reported)

            async def original(*args, **kwargs):
                return result

            wrapped = runner.guard_prompt_count(original)
            with contextlib.redirect_stdout(io.StringIO()):
                actual = asyncio.run(wrapped(client, None, 'x' * 1000, 'x' * 1000, 10, False, tokenizer))
            self.assertEqual(bool(actual.error), expected_error)
        result = types.SimpleNamespace(error='original failure', prompt_tokens=50)

        async def failed(*args, **kwargs):
            return result

        actual = asyncio.run(runner.guard_prompt_count(failed)(client, None, '', 'x' * 2000, 10, False, tokenizer))
        self.assertEqual(actual.error, 'original failure')

    def test_main_replaces_fallback_and_rejects_wrong_package_version(self):
        class Corpus:
            def _get_tokenizer(self, *_):
                raise AssertionError('old fallback must not run')

        invoke = Mock()
        client = type('Client', (), {'run_generation': Mock()})
        modules = {
            'llama_benchy': types.SimpleNamespace(__version__='0.4.0'),
            'llama_benchy.corpus': types.SimpleNamespace(TokenizedCorpus=Corpus),
            'llama_benchy.__main__': types.SimpleNamespace(main=invoke),
            'llama_benchy.client': types.SimpleNamespace(LLMClient=client),
        }
        with (
            patch.dict(sys.modules, modules),
            patch.object(runner, 'strict_tokenizer', return_value='strict') as strict,
        ):
            runner.main()
            self.assertEqual(Corpus()._get_tokenizer('model', 'exact'), 'strict')
            strict.assert_called_once_with('exact')
        invoke.assert_called_once()
        modules['llama_benchy'] = types.SimpleNamespace(__version__='0.4.1')
        with patch.dict(sys.modules, modules):
            with self.assertRaises(RuntimeError):
                runner.main()


class BenchmarkIntegrationTests(unittest.TestCase):
    def test_snapshot_absolute_private_and_only_tokenizer(self):
        bench = load('tested_benchmark_snapshot', 'llama_benchmark.py')
        with tempfile.TemporaryDirectory(prefix='模型 path ') as directory:
            root = Path(directory)
            source = root / 'local model'
            source.mkdir()
            (source / 'tokenizer.json').write_text('{"version":"1.0"}')
            (source / 'weights.safetensors').write_text('must not copy weights')
            output = root / 'reports'
            process = Mock(returncode=1)
            process.poll.return_value = 1
            with (
                patch.object(bench.subprocess, 'Popen', return_value=process) as popen,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    bench.run(
                        '/mock/uvx', bench.DEFAULT_URL, 'alias', str(source), [0, 10, 20, 40], output, {'kind': 'local'}
                    ),
                    1,
                )
            args = popen.call_args.args[0]
            snapshot = Path(args[args.index('--tokenizer') + 1])
            self.assertTrue(snapshot.is_absolute())
            self.assertNotEqual(snapshot, source)
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o700)
            self.assertEqual((snapshot / 'tokenizer.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(snapshot.iterdir()), [snapshot / 'tokenizer.json'])
            self.assertEqual((snapshot / 'tokenizer.json').read_bytes(), (source / 'tokenizer.json').read_bytes())
            metadata = json.loads((snapshot.parent / 'run.json').read_text())
            self.assertEqual(metadata['tokenizer_original'], str(source))
            self.assertEqual(metadata['tokenizer_resolution'], {'kind': 'local'})
            self.assertEqual(metadata['runs'], 1)
            self.assertEqual(len(metadata['depths']), 4)

    def test_unreadable_local_tokenizer_never_starts_benchmark(self):
        bench = load('tested_benchmark_permissions', 'llama_benchmark.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'local'
            source.mkdir()
            (source / 'tokenizer.json').write_text('{}')
            original = Path.open

            def restricted(path, *args, **kwargs):
                if path == source / 'tokenizer.json':
                    raise PermissionError('denied')
                return original(path, *args, **kwargs)

            with patch.object(Path, 'open', restricted), patch.object(bench.subprocess, 'Popen') as popen:
                with self.assertRaises(PermissionError):
                    bench.run('/mock/uvx', bench.DEFAULT_URL, 'alias', str(source), [0, 1, 2, 4], root / 'reports')
                popen.assert_not_called()

    def test_explicit_model_root_overrides_saved_setting(self):
        bench = load('tested_benchmark_explicit_root', 'llama_benchmark.py')
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(
                    bench.sys, 'argv', ['benchmark', '--models-dir', directory, '--base-url', bench.DEFAULT_URL]
                ),
                patch.object(bench.shutil, 'which', return_value='/mock/uvx'),
                patch.object(bench, 'default_model_root', side_effect=AssertionError('显式目录不能读默认设置')),
                patch.object(bench, 'discover', return_value=[{'id': 'llm/Model'}]),
                patch.object(bench, 'resolve_tokenizer', side_effect=ValueError('测试停止')) as resolve,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(bench.main(), 1)
                self.assertEqual(resolve.call_args.args[1], Path(directory))

    def test_main_discovery_failure_returns_once_without_running(self):
        bench = load('tested_benchmark_main_failure', 'llama_benchmark.py')
        for error in (TimeoutError('timeout'), ValueError('bad models')):
            with (
                patch.object(bench.sys, 'argv', ['benchmark']),
                patch.object(bench.shutil, 'which', return_value='/mock/uvx'),
                patch('builtins.input', return_value='http://local/v1') as prompt,
                patch.object(bench, 'discover', side_effect=error) as discover_mock,
                patch.object(bench, 'run') as run,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(bench.main(), 1)
                discover_mock.assert_called_once()
                prompt.assert_called_once()
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
