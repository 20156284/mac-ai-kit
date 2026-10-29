"""负载与报告口径离线回归，不执行推理。"""

import json
from pathlib import Path
import tempfile
import unittest
from test_mlx_benchmark import FakeClient, bench
import report_html


class AlignmentTests(unittest.TestCase):
    def test_input_output_and_warmup(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as folder:
            result = bench.run_benchmark(client, 'mock', folder)
            csv = (Path(folder) / 'results.csv').read_text()
        self.assertEqual(result['config']['warmup_generations'], 1)
        self.assertEqual(result['config']['repetitions'], 1)
        self.assertEqual(result['config']['initial_server_state'], 'unknown')
        self.assertEqual([r['background_depth'] for r in result['results']], [0, 50000, 100000, 200000])
        self.assertEqual([r['target_tokens'] for r in result['results']], [2048, 52048, 102048, 202048])
        for r in result['results']:
            self.assertLessEqual(abs(r['token_accuracy']['prepared_content_tokens'] - r['target_tokens']), 8)
            self.assertEqual(r['output_target'], 480)
            self.assertEqual(r['actual_output_tokens'], 7)
            self.assertEqual(r['output_status'], 'short_output')
            self.assertIsNone(r['finish_reason'])
            self.assertFalse(report_html._eligible(r, 'tg'))
        self.assertEqual(sum(e == '/chat/completions' for e, _, _ in client.calls), 8)
        self.assertIn('actual_output_tokens', csv)
        self.assertIn('finish_reason', csv)

    def test_exact_output_and_finish_reason(self):
        client = FakeClient()
        orig = client.request

        def req(endpoint, payload=None, root=False):
            reply = orig(endpoint, payload, root)
            if endpoint == '/chat/completions':
                reply['timings']['predicted_n'] = 480
                reply['choices'][0]['finish_reason'] = 'length'
            return reply

        client.request = req
        with tempfile.TemporaryDirectory() as folder:
            report = bench.run_benchmark(client, 'mock', folder)
        for row in report['results']:
            self.assertEqual(row['finish_reason'], 'length')
            self.assertEqual(row['output_status'], 'target_reached')
            self.assertTrue(report_html._eligible(row, 'tg'))
        html = report_html.render_report(report)
        self.assertIn('输出目标 480', html)
        self.assertIn('每档先生成预热 1 次', html)
        self.assertIn('2,048 / 52,048 / 102,048 / 202,048', html)

    def test_new_context_budget(self):
        client = FakeClient(ctx=53000)
        with tempfile.TemporaryDirectory() as folder:
            report = bench.run_benchmark(client, 'mock', folder)
        self.assertEqual(
            [r['status'] for r in report['results']], ['success', 'context_limit', 'context_limit', 'context_limit']
        )
        self.assertEqual(sum(e == '/chat/completions' for e, _, _ in client.calls), 2)
