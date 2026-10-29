"""固定提交、原仓库名与双版本共享证据回归测试。"""

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class H3Catalog(unittest.TestCase):
    def test_snapshots_and_payloads(self):
        models = {x['id']: x for x in json.loads((ROOT / 'catalog.json').read_text())['models']}
        a = models['h3-mlx-8bit']
        b = models['h3-ref2va-mlx-8bit']
        self.assertEqual(a['revision'], 'caa4d86c9770a97ff35a610aa5dfcd63d0e2815a')
        self.assertEqual(b['revision'], '10283766961b5c4015d0ea1f9e12b915b634360d')
        for m in (a, b):
            self.assertEqual(m['reuse_family'], 'minimax-h3-mlx-serve')
            self.assertEqual(set(m['include']), set(m['expected_files']))
            self.assertEqual(m['verified_size_bytes'], sum(v['bytes'] for v in m['expected_files'].values()))
            self.assertEqual(m['subdir'], 'video/' + m['repo_id'].split('/')[-1])
        shared = [n for n, v in a['expected_files'].items() if b['expected_files'].get(n) == v]
        self.assertEqual(len(shared), 10)
        self.assertEqual(sum(a['expected_files'][n]['bytes'] for n in shared), 34047317046)
        self.assertNotIn('transformer.safetensors', shared)
        self.assertNotIn('config.json', shared)
        self.assertNotIn('turbo_lora.safetensors', b['expected_files'])
        self.assertEqual(a['verified_size_bytes'] + b['verified_size_bytes'] - 34047317046, 105325139013)


if __name__ == '__main__':
    unittest.main()
