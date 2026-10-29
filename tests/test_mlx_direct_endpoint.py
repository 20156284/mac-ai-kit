"""MLX 原生 API 入口回归；不访问网络、不启动服务、不执行推理。"""

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import mlx_benchmark as bench


class DirectEndpointTests(unittest.TestCase):
    def invoke(self, arguments=(), entered=''):
        proxy = Mock(return_value='http://127.0.0.1:8080/v1')
        captured = []
        real_client = bench.Client

        def make_client(url, timeout):
            captured.append(url)
            return real_client(url, timeout)

        output = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(sys.modules, {'llama_benchmark': types.SimpleNamespace(default_url=proxy)}),
            patch('builtins.input', return_value=entered) as prompt,
            patch.object(bench, 'Client', side_effect=make_client),
            patch.object(bench, 'choose_model', return_value='test-model'),
            patch.object(bench, 'run_benchmark', return_value={'results': []}),
            contextlib.redirect_stdout(output),
        ):
            code = bench.main(['--output', folder, *arguments])
        self.assertEqual(code, 0)
        proxy.assert_not_called()
        return captured, prompt, output.getvalue()

    def test_default_ignores_managed_proxy_and_directory_arguments(self):
        urls, prompt, output = self.invoke(['--root', '/not-read/AI', '--models-dir', '/not-read/models'])
        self.assertEqual(urls, ['http://127.0.0.1:11234/v1'])
        self.assertIn('11234', prompt.call_args.args[0])
        self.assertNotIn('沿用托管代理', output)

    def test_custom_app_port_from_prompt(self):
        urls, _, _ = self.invoke(entered='  http://127.0.0.1:9001/v1  ')
        self.assertEqual(urls, ['http://127.0.0.1:9001/v1'])

    def test_explicit_address_skips_prompt(self):
        urls, prompt, _ = self.invoke(['--base-url', 'http://127.0.0.1:9010/v1'])
        self.assertEqual(urls, ['http://127.0.0.1:9010/v1'])
        prompt.assert_not_called()

    def test_connection_failure_explains_app_api_without_starting_test(self):
        output = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(
                bench,
                'choose_model',
                side_effect=urllib.error.URLError(ConnectionRefusedError(61, 'Connection refused')),
            ),
            patch.object(bench, 'run_benchmark') as run,
            contextlib.redirect_stderr(output),
        ):
            code = bench.main(['--base-url', 'http://127.0.0.1:9001/v1', '--output', folder])
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertIn('http://127.0.0.1:9001/v1', output.getvalue())
        self.assertIn('开启 API', output.getvalue())
        self.assertIn('无需启动 llama-swap', output.getvalue())


if __name__ == '__main__':
    unittest.main()
