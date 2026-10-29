"""纯内存模拟测试；不访问网络，不加载或调用模型。"""

import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import urllib.error
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import mlx_benchmark as bench


class FakeClient:
    base_url = 'http://localhost:8000/v1'
    timeout = 1800

    def __init__(self, tokenize=True, failure=None, ctx=300000):
        self.calls = []
        self.tokenize = tokenize
        self.failure = failure
        self.ctx = ctx

    def request(self, endpoint, payload=None, root=False):
        self.calls.append((endpoint, payload, root))
        if endpoint == '/models':
            return {'data': [{'id': 'mock-only'}]}
        if endpoint.startswith('/props'):
            return {'default_generation_settings': {'n_ctx': self.ctx}}
        if endpoint == '/tokenize':
            if not self.tokenize:
                raise bench.APIError('不支持', 404)
            return {'tokens': [1] * max(1, len(payload['content']) // 4)}
        if endpoint == '/chat/completions':
            if self.failure:
                raise self.failure
            count = len(payload['messages'][0]['content']) // 4 + 12
            return {
                'choices': [{'message': {'content': 'mock-only'}}],
                'timings': {
                    'prompt_n': count,
                    'cached_n': 0,
                    'prompt_ms': 1000,
                    'prompt_per_second': count,
                    'predicted_n': 7,
                    'predicted_ms': 100,
                    'predicted_per_second': 70,
                    'tokenize_ms': 20,
                },
            }
        raise AssertionError(endpoint)


class Tests(unittest.TestCase):
    def test_exact_eight_generations_and_incremental_report(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as folder:
            result = bench.run_benchmark(client, 'mock-only', folder)
            stored = json.loads((Path(folder) / 'results.json').read_text())
            self.assertEqual([r['target_tokens'] for r in stored['results']], [2048, 52048, 102048, 202048])
            self.assertTrue((Path(folder) / 'results.csv').exists())
        calls = [p for endpoint, p, _ in client.calls if endpoint == '/chat/completions']
        self.assertEqual(len(calls), 8)
        self.assertEqual(len({p['messages'][0]['content'][:64] for p in calls}), 8)
        for call in calls:
            self.assertIs(call['stream'], False)
            self.assertIs(call['enable_thinking'], False)
            self.assertEqual(call['temperature'], 0)
            self.assertEqual(call['max_tokens'], 480)
        for row in result['results']:
            self.assertEqual(row['status'], 'success')
            self.assertEqual(row['metrics']['estimated_server_ttft_ms'], 1020)
            self.assertFalse(row['context_check']['passed'])

    def test_invalid_generation_response_stops(self):
        for value in ({'unexpected': 'object'}, {'choices': [{'message': {'content': None}}]}):
            client = FakeClient()
            original = client.request

            def request(endpoint, payload=None, root=False):
                reply = original(endpoint, payload, root)
                return value if endpoint == '/chat/completions' else reply

            client.request = request
            with tempfile.TemporaryDirectory() as folder:
                report = bench.run_benchmark(client, 'mock', folder)
            self.assertEqual(sum(e == '/chat/completions' for e, _, _ in client.calls), 1)
            self.assertEqual(report['results'][1]['status'], 'not_run')

    def test_json_parse_error_stops(self):
        client = FakeClient(failure=bench.APIError('非法 JSON', body='bad'))
        with tempfile.TemporaryDirectory() as folder:
            report = bench.run_benchmark(client, 'mock', folder)
        self.assertEqual(sum(e == '/chat/completions' for e, _, _ in client.calls), 1)
        self.assertEqual(report['results'][1]['status'], 'not_run')

    def test_missing_timings_not_successful_measurement(self):
        client = FakeClient()
        original = client.request

        def request(endpoint, payload=None, root=False):
            reply = original(endpoint, payload, root)
            if endpoint == '/chat/completions':
                reply.pop('timings')
            return reply

        client.request = request
        with tempfile.TemporaryDirectory() as folder:
            report = bench.run_benchmark(client, 'mock', folder)
        self.assertTrue(
            report['warmups'][0]['status'] == 'invalid_metrics'
            and all(r['status'] == 'not_run' for r in report['results'])
        )

    def test_final_calibration_limit_rechecked(self):
        client = FakeClient(ctx=50710)
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(bench, 'prepare_prompt', return_value=('x' * 210000, {'method': 'tokenize_content'})):
                report = bench.run_benchmark(client, 'mock', folder)
        self.assertEqual(sum(e == '/chat/completions' for e, _, _ in client.calls), 0)
        self.assertEqual(report['results'][0]['status'], 'context_limit')

    def test_invalid_final_tokenize_not_sent(self):
        client = FakeClient()
        original = client.request

        def request(endpoint, payload=None, root=False):
            if endpoint == '/tokenize':
                return {'tokens': 'not-an-array'}
            return original(endpoint, payload, root)

        client.request = request
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(bench, 'prepare_prompt', return_value=('x' * 100, {'method': 'tokenize_content'})):
                report = bench.run_benchmark(client, 'mock', folder)
        self.assertEqual(sum(e == '/chat/completions' for e, _, _ in client.calls), 0)
        self.assertTrue(
            report['warmups'][0]['status'] == 'error' and all(r['status'] == 'not_run' for r in report['results'])
        )

    def test_uncertain_gateway_and_decode_error_stop(self):
        for error in (bench.APIError('网关超时', 504), UnicodeDecodeError('utf-8', b'\xff', 0, 1, '模拟解码错误')):
            client = FakeClient(failure=error)
            with tempfile.TemporaryDirectory() as folder:
                report = bench.run_benchmark(client, 'mock', folder)
            self.assertEqual(sum(e == '/chat/completions' for e, _, _ in client.calls), 1)
            self.assertEqual(report['results'][1]['status'], 'not_run')

    def test_direct_default_url_and_explicit_override(self):
        for explicit in (False, True):
            client = FakeClient()
            helper = unittest.mock.Mock(return_value='http://localhost:9999/v1')
            module = types.SimpleNamespace(default_url=helper)
            args = ['--root', '/tmp/mock-root', '--models-dir', '/tmp/mock-models']
            if explicit:
                args += ['--base-url', 'http://localhost:9998/v1']
            with (
                patch.dict('sys.modules', {'llama_benchmark': module}),
                patch.object(bench, 'Client', return_value=client) as factory,
                patch('builtins.input', side_effect=[] if explicit else ['']),
                patch.object(bench, 'run_benchmark', return_value={'results': []}),
            ):
                self.assertEqual(bench.main(args), 0)
            if explicit:
                helper.assert_not_called()
                self.assertEqual(factory.call_args.args[0], 'http://localhost:9998/v1')
            else:
                helper.assert_not_called()
                self.assertEqual(factory.call_args.args[0], 'http://127.0.0.1:11234/v1')

    def test_tokenize_unavailable_is_estimated(self):
        _, accuracy = bench.prepare_prompt(FakeClient(tokenize=False), 'mock', 50000, 'nonce')
        self.assertEqual(accuracy['method'], 'estimated_chars')
        self.assertIsNone(accuracy['prepared_content_tokens'])

    def test_tokenize_auth_failure_is_not_silent_fallback(self):
        client = FakeClient()
        client.request = lambda *args, **kwargs: (_ for _ in ()).throw(bench.APIError('拒绝', 401))
        with self.assertRaises(bench.APIError):
            bench.prepare_prompt(client, 'mock', 50000, 'nonce')

    def test_missing_metrics_remain_unknown(self):
        actual, metrics, cache = bench.parse_response({'usage': {'prompt_tokens': 50002}}, 50000, 1)
        self.assertEqual(actual, 50002)
        self.assertIsNone(metrics['prompt_per_second'])
        self.assertIsNone(metrics['estimated_server_ttft_ms'])
        self.assertEqual(cache, 'unknown')

    def test_cache_contamination(self):
        for cached, expected in [(0, 'clean'), (10, 'partial_cache'), (11, 'cache_contaminated')]:
            _, _, cache = bench.parse_response({'timings': {'prompt_n': 100, 'cached_n': cached}}, 100, 1)
            self.assertEqual(cache, expected)

    def test_timeout_does_not_retry_or_overlap(self):
        client = FakeClient(failure=TimeoutError('模拟超时'))
        with tempfile.TemporaryDirectory() as folder:
            report = bench.run_benchmark(client, 'mock-only', folder)
        self.assertEqual(sum(e == '/chat/completions' for e, _, _ in client.calls), 1)
        self.assertEqual([r['status'] for r in report['results']], ['not_run'] * 4)

    def test_known_http_error_no_retry(self):
        client = FakeClient(failure=bench.APIError('模拟拒绝', 400, 'bad'))
        with tempfile.TemporaryDirectory() as folder:
            report = bench.run_benchmark(client, 'mock-only', folder)
        self.assertEqual(sum(e == '/chat/completions' for e, _, _ in client.calls), 1)
        self.assertTrue(
            report['warmups'][0]['status'] == 'error' and all(r['status'] == 'not_run' for r in report['results'])
        )

    def test_interrupt_persists_partial(self):
        client = FakeClient(failure=KeyboardInterrupt())
        with tempfile.TemporaryDirectory() as folder:
            report = bench.run_benchmark(client, 'mock-only', folder)
            self.assertTrue((Path(folder) / 'results.json').exists())
        self.assertEqual([r['status'] for r in report['results']], ['not_run'] * 4)

    def test_context_preflight(self):
        client = FakeClient(ctx=100000)
        with tempfile.TemporaryDirectory() as folder:
            report = bench.run_benchmark(client, 'mock-only', folder)
        self.assertEqual(sum(e == '/chat/completions' for e, _, _ in client.calls), 4)
        self.assertEqual(
            [r['status'] for r in report['results']], ['success', 'success', 'context_limit', 'context_limit']
        )

    def test_models_selection(self):
        client = FakeClient()
        self.assertEqual(bench.choose_model(client), 'mock-only')
        client.request = lambda *args: {'data': [{'id': 'a'}, {'id': 'b'}]}
        self.assertEqual(bench.choose_model(client, input_fn=lambda _: '2'), 'b')
        with self.assertRaises(ValueError):
            bench.choose_model(client, requested='c')

    def test_remote_hardware_unknown(self):
        self.assertEqual(bench.hardware_info('https://example.com/v1')['scope'], 'unknown_remote')

    def test_url_normalization(self):
        self.assertEqual(bench.Client('http://localhost:8000/v1').root, 'http://localhost:8000')
        self.assertEqual(bench.Client('http://localhost:8000').base_url, 'http://localhost:8000/v1')
        with self.assertRaises(ValueError):
            bench.Client('http://user:secret@localhost:8000')

    def test_invalid_metric_not_zero(self):
        _, metrics, _ = bench.parse_response({'timings': {'prompt_ms': float('nan'), 'predicted_n': -1}}, 50000, 1)
        self.assertIsNone(metrics['prompt_ms'])
        self.assertIsNone(metrics['predicted_n'])


if __name__ == '__main__':
    unittest.main()
