import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('desktop_inventory', ROOT / 'scripts/api_inventory.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class DesktopOnlyTests(unittest.TestCase):
    def test_templates_have_no_mlx_cli_recipe(self):
        templates = json.loads((ROOT / 'config/api_templates.json').read_text())
        runtime = next(x for x in templates['runtimes'] if x['id'] == 'mlx-serve')
        self.assertEqual(runtime['probes'], [])
        self.assertTrue(all(x.endswith('.app') for x in runtime['standard_probes']))
        for text in [runtime['markdown']] + [
            x['markdown'] for x in templates['models'].values() if 'mlx-serve' in x.get('runtime_ids', [])
        ]:
            self.assertNotIn('--serve', text)
            self.assertNotIn('MLX_SERVE_MTP_HEAD_QUANT_BITS', text)
        self.assertIn('手动打开', runtime['markdown'])

    def test_external_model_root_still_detects_standard_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            models = base / 'external/models'
            models.mkdir(parents=True)
            app = base / 'Applications/MLX-Serve.app'
            app.mkdir(parents=True)
            templates = {'runtimes': [{'id': 'mlx-serve', 'probes': [], 'standard_probes': [str(app)]}]}
            _, _, _, runtimes = api.inventory(models, {'models': []}, templates)
            self.assertEqual(runtimes[0]['detected_path'], str(app))

    def test_example_has_no_mlx_process(self):
        example = (ROOT / 'config/llama-swap.example.yaml').read_text()
        self.assertNotIn('mlx-service', example)
        self.assertNotIn('start-mlx', example)
