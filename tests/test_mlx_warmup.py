"""预热隔离与失败停止的离线回归。"""
import csv
import tempfile
import unittest
from pathlib import Path
from test_mlx_benchmark import FakeClient, bench

class WarmupTests(unittest.TestCase):
    def test_warmups_separate_from_scores(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as folder:
            report = bench.run_benchmark(client, 'mock', folder)
            with (Path(folder)/'results.csv').open(encoding='utf-8-sig') as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 4)
        self.assertEqual(len(report['warmups']), 4)
        self.assertTrue(all(r['phase']=='warmup' for r in report['warmups']))
        self.assertTrue(all(r['phase']=='measurement' for r in report['results']))
        for w,r in zip(report['warmups'],report['results']):
            self.assertNotEqual(w['prompt_sha256'],r['prompt_sha256'])
            self.assertEqual(w['target_tokens'],r['target_tokens'])
            self.assertEqual(w['status'],'success')
    def test_second_warmup_timeout_preserves_first_score(self):
        client=FakeClient(); orig=client.request; count=0
        def req(endpoint,payload=None,root=False):
            nonlocal count
            if endpoint=='/chat/completions':
                count+=1
                if count==3: raise TimeoutError('模拟预热超时')
            return orig(endpoint,payload,root)
        client.request=req
        with tempfile.TemporaryDirectory() as folder:
            report=bench.run_benchmark(client,'mock',folder)
        self.assertEqual(count,3)
        self.assertEqual([r['status'] for r in report['results']],['success','not_run','not_run','not_run'])
        self.assertEqual(report['warmups'][1]['status'],'error')
    def test_warmup_http_rejection_stops(self):
        client=FakeClient(failure=bench.APIError('模拟拒绝',400))
        with tempfile.TemporaryDirectory() as folder:
            report=bench.run_benchmark(client,'mock',folder)
        self.assertEqual(sum(e=='/chat/completions' for e,_,_ in client.calls),1)
        self.assertTrue(all(r['status']=='not_run' for r in report['results']))
