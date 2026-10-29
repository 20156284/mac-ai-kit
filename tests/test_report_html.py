"""模拟样本仅验证格式，不代表真实基准结果。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from report_html import render_report, write_report, _metric


class ReportTests(unittest.TestCase):
    def fixture(self):
        return {
            'metadata': {'model': 'TEST ONLY', 'hardware': None, 'server_props': {'n_ctx': 262144}},
            'config': {'targets': [50000, 100000, 200000]},
            'results': [
                {
                    'target_tokens': 50000,
                    'actual_prompt_tokens': 49999,
                    'status': 'success',
                    'cache_status': 'clean',
                    'metrics': {
                        'prompt_per_second': 1234.5,
                        'predicted_per_second': 45.6,
                        'cached_n': 0,
                        'predicted_n': 192,
                        'prompt_ms': 5000,
                        'tokenize_ms': 200,
                        'estimated_server_ttft_ms': 5200,
                    },
                }
            ],
        }

    def test_schema_and_metric_mapping(self):
        fixture = self.fixture()
        output = render_report(fixture)
        for text in ['49,999', '1,234.5', '45.6', '5.20', '262144', '缓存：无缓存命中']:
            self.assertIn(text, output)
        self.assertNotIn('M5', output)
        self.assertEqual(_metric(fixture['results'][0], 'cached'), 0)

    def test_escape_every_untrusted_field(self):
        attack = '<script>alert("X")</script><img src="https://evil.example">'
        report = self.fixture()
        report['metadata']['model'] = attack
        report['metadata']['hardware'] = attack
        report['metadata']['server_props']['n_ctx'] = attack
        report['results'][0].update(error=attack, status=attack, cache_status=attack, token_accuracy={'note': attack})
        report['config_source'] = attack
        output = render_report(report)
        self.assertNotIn('<script', output)
        self.assertNotIn('<img', output)
        self.assertIn('&lt;script&gt;', output)

    def test_offline_and_missing(self):
        output = render_report({})
        for text in ['<script', '<link', '<iframe', 'src=', 'url(', 'http://', 'https://']:
            self.assertNotIn(text, output)
        self.assertIn('尚无有效实测数据', output)
        self.assertIn('未知：服务未提供硬件信息', output)
        self.assertIn('跨轮漂移：未测量', output)
        self.assertIn('标准差：未测量', output)
        self.assertNotIn('0.00 <span>s', output)

    def test_ttft_requires_both_components(self):
        self.assertIsNone(_metric({'metrics': {'prompt_ms': 30}}, 'ttft'))
        self.assertEqual(_metric({'metrics': {'prompt_ms': 30, 'tokenize_ms': 4}}, 'ttft'), 34)

    def test_invalid_numbers_are_not_plotted(self):
        report = self.fixture()
        report['results'][0]['metrics'] = {
            'predicted_per_second': float('nan'),
            'prompt_per_second': -1,
            'estimated_server_ttft_ms': float('inf'),
        }
        output = render_report(report)
        self.assertIn('尚无有效实测数据', output)
        self.assertNotIn('<circle', output)

    def test_cache_and_failed_exclusion(self):
        report = self.fixture()
        report['results'][0]['cache_status'] = 'cache_contaminated'
        output = render_report(report)
        self.assertIn('1,234.5', output)  # 保留原始表格值。
        self.assertIn('警告：缓存污染', output)
        self.assertNotIn('1,234.5 <span>tok/s', output)
        report['results'][0]['status'] = 'error'
        self.assertNotIn('45.6 <span>tok/s', render_report(report))

    def test_props_settings_and_context(self):
        report = self.fixture()
        report['metadata']['hardware'] = {'scope': 'not_collected'}
        report['metadata']['server_props']['settings'] = {
            'kv_quant': 'q8',
            'kv_attn_mode': 'quantized',
            'mtp': {'loaded': True, 'default_on': False, 'depth': 3},
            'pld': {'default_on': True, 'draft_len': 5},
        }
        report['results'][0]['context_check'] = {'passed': False}
        output = render_report(report)
        for text in [
            '未采集硬件',
            'q8',
            'quantized',
            '已加载',
            '默认关闭',
            '草稿长度 5',
            '上下文验证',
            '<td>未通过</td>',
        ]:
            self.assertIn(text, output)

    def test_write_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_report(self.fixture(), Path(tmp) / 'nested' / 'report.html')
            self.assertEqual(path.read_text(encoding='utf-8'), render_report(self.fixture()))

    def test_missing_sample_breaks_chart(self):
        report = self.fixture()
        report['results'].extend(
            [
                {'target_tokens': 100000, 'status': 'error'},
                {'target_tokens': 200000, 'metrics': {'predicted_per_second': 20}},
            ]
        )
        output = render_report(report)
        self.assertNotIn('stroke-width="3"', output)


if __name__ == '__main__':
    unittest.main()
