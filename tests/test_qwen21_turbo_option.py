"""官方Turbo完整组件、透明描述、排序与MOSS移除。"""

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Qwen21TurboOption(unittest.TestCase):
    def test_turbo_bundle_order_and_alpha(self):
        models = json.loads((ROOT / 'catalog.json').read_text())['models']
        by = {m['id']: m for m in models}
        self.assertFalse(any(m['id'].startswith('moss-') for m in models))
        ids = [m['id'] for m in models]
        self.assertLess(ids.index('qwen21-turbo'), ids.index('krea2-turbo'))
        main = by['qwen21-turbo']
        self.assertIn('Turbo', main['short_name'])
        self.assertIn('透明背景', main['short_name'])
        self.assertEqual(main['requires'], ['qwen21-text', 'qwen21-vae', 'qwen21-license'])
        total = 0
        for key in ['qwen21-turbo', 'qwen21-text', 'qwen21-vae']:
            m = by[key]
            self.assertEqual(m['repo_id'], 'Comfy-Org/Qwen-Image-2.1')
            self.assertEqual(m['subdir'], 'images/Qwen-Image-2.1')
            self.assertTrue(m['verification']['revision_is_mutable'])
            for f in m['expected_files'].values():
                total += f['bytes']
                self.assertEqual(len(f['sha256']), 64)
        self.assertEqual(total, 32440124920)
        self.assertIn('vae/qwen_image_2.1_vae_bf16.safetensors', by['qwen21-vae']['expected_files'])


if __name__ == '__main__':
    unittest.main()
