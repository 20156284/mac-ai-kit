"""原生切换客户端离线测试；不发真实 HTTP 或推理。"""
import importlib.util
import io
import contextlib
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
spec = importlib.util.spec_from_file_location('sd_models', Path(__file__).resolve().parents[1] / 'scripts/sd_models.py')
sd = importlib.util.module_from_spec(spec); spec.loader.exec_module(sd)

class SDModelTests(unittest.TestCase):
    def files(self, recipe):
        return [dict(relative_path=p, category=p.split('/')[0], id='@opaque/' + p) for p in sd.SELECTIONS[recipe].values()]

    def test_opaque_ids_not_constructed(self):
        for recipe in sd.SELECTIONS:
            files = self.files(recipe)
            self.assertEqual(sd.selection(recipe, files)['models']['vae'], files[-1]['id'])

    def test_duplicate_missing_wrong_category_rejected(self):
        files = self.files('krea2-turbo')
        for bad in (files[:-1], files + [files[0]], [dict(f, category='wrong') for f in files]):
            with self.assertRaises(ValueError): sd.selection('krea2-turbo', bad)

    def test_load_gets_ids_then_one_native_post(self):
        with patch.object(sd, 'request', side_effect=[{'recipes':[{'id':'krea2-turbo'}]}, {'files':self.files('krea2-turbo')}, {'state':'ready'}]) as call, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sd.main(['load', 'krea2-turbo']), 0)
        self.assertEqual([c.args[1] for c in call.call_args_list], ['recipes', 'model-files', 'models/load'])
        self.assertEqual(call.call_args_list[-1].args[2]['recipe'], 'krea2-turbo')

    def test_busy_does_not_retry_unload_or_cancel(self):
        with patch.object(sd, 'request', side_effect=[{'recipes':[{'id':'krea2-turbo'}]}, {'files':self.files('krea2-turbo')}, ValueError('HTTP 409 model_busy')]) as call, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sd.main(['load','krea2-turbo']), 1)
        self.assertEqual(call.call_count, 3)

    def test_status_only_gets_status(self):
        with patch.object(sd, 'request', return_value={'state':'empty'}) as call, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sd.main(['status']), 0)
        call.assert_called_once_with('http://127.0.0.1:12345', 'models/status')

    def test_http_errors_preserve_error_code(self):
        with patch.object(sd, 'urlopen', side_effect=HTTPError('http://localhost', 409,'busy',{},io.BytesIO(b'{"code":"model_busy"}'))):
            with self.assertRaisesRegex(ValueError, '409.*model_busy'): sd.request('http://localhost', 'models/load', {})
