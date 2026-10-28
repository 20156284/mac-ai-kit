"""Benchmark 启动流程不重复确认；仅模拟调用。"""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import llama_benchmark as llama
import mlx_benchmark as mlx


class PromptFlowTests(unittest.TestCase):
    def test_llama_starts_after_parameters_without_yes(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(llama.sys, 'argv', ['benchmark', '--base-url', llama.DEFAULT_URL, '--output', folder]), \
             patch.object(llama.shutil, 'which', return_value='/bin/uvx'), \
             patch.object(llama, 'discover', return_value=[{'id': 'test-model'}]), \
             patch.object(llama, 'resolve_tokenizer', return_value={'kind': 'local', 'value': '/mock/tokenizer.json'}), \
             patch('builtins.input', side_effect=['']) as prompt, \
             patch.object(llama, 'run', return_value=0) as run, \
             contextlib.redirect_stdout(output):
            self.assertEqual(llama.main(), 0)
            self.assertEqual(prompt.call_count, 1)
            self.assertIn('最大背景深度', prompt.call_args.args[0])
            run.assert_called_once()
        for text in ('确认运行', '请确认该模型', '不使用你的 API Key', '远程接口可能产生费用'):
            self.assertNotIn(text, output.getvalue())
        for text in ('接口：', '模型：', '背景深度：', '报告目录：'):
            self.assertIn(text, output.getvalue())

    def test_mlx_has_no_redundant_start_warning(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(mlx, 'choose_model', return_value='mock'), \
             patch.object(mlx, 'run_benchmark', return_value={'results': []}) as run, \
             patch('builtins.input', side_effect=AssertionError('不应追加确认')), \
             contextlib.redirect_stdout(output):
            self.assertEqual(mlx.main(['--base-url', 'http://localhost:11234/v1', '--output', folder]), 0)
            run.assert_called_once()
        self.assertNotIn('HTTP 请求可能触发', output.getvalue())
        self.assertNotIn('本脚本不启动/停止服务', output.getvalue())
