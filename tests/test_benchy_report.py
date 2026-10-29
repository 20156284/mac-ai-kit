"""仅模拟 CSV，不运行模型或第三方测试器。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from benchy_report import build_report
from report_html import render_report, _eligible


class BenchyReportTests(unittest.TestCase):
    def data(self):
        return (
            ['model', 'test_name', 't_s_mean', 'e2e_ttft_mean'],
            [['fixture', 'pp2048', '848261.86', '1287.38'], ['fixture', 'tg480', '18.34', '1287.38']],
            {'model': 'fixture', 'depths': [0, 50000], 'pp': 2048, 'tg': 480, 'runs': 1},
        )

    def test_same_layout_but_distinct_measurement(self):
        h, r, m = self.data()
        report = build_report(h, r, m)
        page = render_report(report)
        self.assertIn('LLAMA BENCHY', page)
        self.assertIn('客户端端到端', page)
        self.assertIn('848,261.9', page)
        self.assertNotIn('最低服务端估算', page)
        self.assertNotIn('生成预热 0', page)
        self.assertEqual(report['config']['warmup_generations_per_depth'], 1)
        self.assertFalse(_eligible(report['runs'][0], 'pp'))
        self.assertTrue(_eligible(report['runs'][0], 'tg'))
        self.assertIsNone(report['runs'][0]['predicted_n'])

    def test_incomplete_failed_excluded(self):
        h, r, m = self.data()
        report = build_report(h, r[:1], m, failed=True)
        self.assertFalse(_eligible(report['runs'][0], 'tg'))
        self.assertIn('测试未完成', render_report(report))
        self.assertEqual(report['runs'][1]['status'], 'not_run')

    def test_model_mismatch_rejected(self):
        h, r, m = self.data()
        m['model'] = 'other'
        with self.assertRaises(ValueError):
            build_report(h, r, m)

    def test_duplicate_and_invalid_depth_rejected(self):
        h, r, m = self.data()
        with self.assertRaises(ValueError):
            build_report(h, r + r[:1], m)
        r[0][1] = 'pp2048 @ d77'
        with self.assertRaises(ValueError):
            build_report(h, r, m)

    def test_untrusted_text_escaped(self):
        h, r, m = self.data()
        m['model'] = '<script>evil</script>'
        for row in r:
            row[0] = m['model']
        page = render_report(build_report(h, r, m, warning='<script>bad</script>'))
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)

    def test_nan_is_not_performance(self):
        h, r, m = self.data()
        r[1][2] = 'nan'
        report = build_report(h, r, m)
        self.assertFalse(_eligible(report['runs'][0], 'tg'))
        self.assertIsNone(report['runs'][0]['predicted_per_second'])
