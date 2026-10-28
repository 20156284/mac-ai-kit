"""Benchmark 离线校验及模拟进程测试，不发送推理请求。"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bench', ROOT / 'scripts/llama_benchmark.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class BenchmarkTests(unittest.TestCase):
    def test_depths(self):
        self.assertEqual(bench.depths('200000'), [0, 50000, 100000, 200000])
        self.assertEqual(bench.depths('101'), [0, 25, 50, 101])
        for value in ('0', '3', '-1', '20.1', '2000001', 'nan'):
            with self.assertRaises(ValueError):
                bench.depths(value)

    def test_endpoint(self):
        self.assertEqual(bench.endpoint('http://127.0.0.1:11234/v1/'), bench.DEFAULT_URL)
        for value in ('ftp://example.com', 'http://user:pass@x/v1', 'http://x?key=a',
                      'http://x#x', 'http://x?', 'http://x#', 'http://x:99999',
                      'http://x\\evil', 'http://x/\x1b', 'http://x/ a'):
            with self.assertRaises(ValueError):
                bench.endpoint(value)

    def test_names_and_local_tokenizer(self):
        self.assertEqual(bench.identifier('qwen:latest'), 'qwen:latest')
        for value in ('', '--help', 'a\nb'):
            with self.assertRaises(ValueError):
                bench.identifier(value)
        with tempfile.TemporaryDirectory(prefix='tokenizer space ') as directory:
            self.assertEqual(bench.tokenizer_identifier(directory), directory)

    def test_environment_no_ambient_credentials(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy', 'HF_TOKEN': 'dummy',
                                     'HTTPS_PROXY': 'dummy', 'PYTHONPATH': 'dummy',
                                     'UV_INDEX_URL': 'dummy'}):
            env = bench.clean_environment(Path('/tmp/isolated'), Path('/tmp/cache'))
        self.assertFalse(set(env) & {'OPENAI_API_KEY', 'HF_TOKEN', 'HTTPS_PROXY',
                                    'PYTHONPATH', 'UV_INDEX_URL'})
        self.assertEqual(env['HF_HUB_DISABLE_IMPLICIT_TOKEN'], '1')
        self.assertEqual(env['HF_HUB_CACHE'], '/tmp/cache/hub')

    def test_command(self):
        cmd = bench.command('/bin/uvx', bench.DEFAULT_URL, 'model', 'tokenizer',
                            [0, 50, 100, 200], Path('/tmp/report.csv'))
        self.assertIn('llama-benchy==0.4.0', cmd)
        self.assertNotIn('--api-key', cmd)
        self.assertEqual(cmd[cmd.index('--runs') + 1], '1')
        self.assertEqual(cmd[cmd.index('--depth') + 1:cmd.index('--pp')],
                         ['0', '50', '100', '200'])
        self.assertIn('--exit-on-first-fail', cmd)
        self.assertEqual(cmd[cmd.index('--concurrency') + 1], '1')

    def test_cancel_has_no_subprocess_or_directory(self):
        with patch.object(bench.shutil, 'which', return_value='/bin/uvx'), \
                patch('builtins.input', side_effect=['', '', 'q']), \
                patch.object(bench, 'discover', return_value=[{'id': 'org/model'}]), \
                patch.object(bench, 'resolve_tokenizer', return_value={'kind': 'public', 'value': 'org/model'}), \
                patch.object(bench.subprocess, 'Popen') as popen, \
                patch.object(bench.sys, 'argv', ['benchmark']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bench.main(), 0)
            popen.assert_not_called()

    def test_mock_run_and_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / 'uvx'
            executable.write_text('''#!/usr/bin/env python3
import csv, json, os, pathlib, sys
args = sys.argv[1:]
pathlib.Path('arguments.json').write_text(json.dumps(args))
pathlib.Path('environment.json').write_text(json.dumps(dict(os.environ)))
print('mock stdout')
print('mock stderr', file=sys.stderr)
p = pathlib.Path(args[args.index('--save-result') + 1])
points = args[args.index('--depth') + 1:args.index('--pp')]
with p.open('w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['model', 'test_name', 't_s_mean', 'e2e_ttft_mean'])
    for d in points:
        suffix = ' @ d' + d if d != '0' else ''
        w.writerow(['model', 'pp2048' + suffix, '100.25', '200.5'])
        w.writerow(['model', 'tg480' + suffix, '40.25', ''])
''')
            executable.chmod(0o700)
            output = root / 'reports'
            stream = io.StringIO()
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy'}), contextlib.redirect_stdout(stream):
                for _ in range(2):
                    self.assertEqual(bench.run(str(executable), bench.DEFAULT_URL,
                                              'model', 'tokenizer', [0, 50, 100, 200], output), 0)
            folders = sorted(output.glob('llama-benchmark-*'))
            self.assertEqual(len(folders), 2)
            self.assertEqual(len(list(output.glob('llama-benchmark-*/*.csv'))), 2)
            for folder in folders:
                self.assertIn('LLAMA BENCHY', (folder / 'report.html').read_text())
                self.assertEqual((folder / 'stdout.log').read_text(), 'mock stdout\n')
                self.assertEqual((folder / 'stderr.log').read_text(), 'mock stderr\n')
                env = json.loads((folder / 'environment.json').read_text())
                self.assertNotIn('OPENAI_API_KEY', env)
                self.assertEqual(env['HF_HUB_CACHE'], str(output / '.llama-benchy-cache/hub'))
                headers, rows = bench.read_csv(next(folder.glob('*.csv')))
                with self.assertRaises(ValueError):
                    bench.summary(headers, rows, [0, 50, 100, 200], 'other-model')
                records = bench.summary(headers, rows, [0, 50, 100, 200], 'model')
                self.assertEqual(len(bench.table(records, 30).splitlines()), 6)
                self.assertLessEqual(max(map(len, bench.table(records, 30).splitlines())), 30)
                with self.assertRaises(ValueError):
                    bench.summary(headers, rows[:-1], [0, 50, 100, 200], 'model')
                (folder / 'stdout.log').write_text("Falling back to 'gpt2' tokenizer as approximation.\n")
                self.assertTrue(bench.unreliable_logs(folder))

    def test_invalid_csv_and_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.csv'
            for value in ('a,b\n1\n', 'a,a\n1,2\n', '\x1b[32mtest\n1\n', 'a,b\n"unterminated'):
                path.write_text(value)
                with self.assertRaises((ValueError, bench.csv.Error)):
                    bench.read_csv(path)
            link = Path(directory) / 'link'
            link.symlink_to(directory, target_is_directory=True)
            with self.assertRaises(ValueError):
                bench.output_directory(str(link / 'reports'))
        for path in ('relative', '/', '/tmp/../home'):
            with self.assertRaises(ValueError):
                bench.output_directory(path)

    def test_default_url_ignores_existing_proxy_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'AI'
            models = Path(directory) / 'models'
            (root / 'bin').mkdir(parents=True)
            (models / 'llm').mkdir(parents=True)
            server = root / 'bin/llama-server'
            server.write_text('#!/bin/sh\nexit 99\n')
            server.chmod(0o700)
            model = models / 'llm/test.gguf'
            model.write_bytes(b'not weights')
            from service_manager import Manager
            manager = Manager(root, models)
            with contextlib.redirect_stdout(io.StringIO()):
                manager.configure(model, port=8099, bind='0.0.0.0')
            before = manager.settings.read_bytes()
            with patch.object(Manager, 'loaded', side_effect=AssertionError('no launchd')), patch.object(bench.subprocess, 'run', side_effect=AssertionError('no execution')), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual('http://127.0.0.1:11234/v1', bench.default_url(root, models))
                data = json.loads(before)
                del data['proxy_bind']
                manager.settings.write_text(json.dumps(data))
                self.assertEqual('http://127.0.0.1:11234/v1', bench.default_url(root, models))
                manager.settings.write_bytes(before)
                self.assertEqual(before, manager.settings.read_bytes())
                self.assertEqual(bench.DEFAULT_URL, bench.default_url(Path(directory) / 'absent', models))
                data['port'] = 80
                manager.settings.write_text(json.dumps(data))
                self.assertEqual(bench.DEFAULT_URL, bench.default_url(root, models))

    def test_explicit_url_and_prompt_override_direct_default(self):
        for argv, response in [(['benchmark', '--base-url', 'http://127.0.0.1:9010/v1'], None), (['benchmark'], 'http://127.0.0.1:9010/v1')]:
            with patch.object(bench.sys, 'argv', argv), patch.object(bench.shutil, 'which', return_value='/bin/uvx'), patch.object(bench, 'default_url', return_value='http://127.0.0.1:11234/v1') as default, patch('builtins.input', return_value=response) as ask, patch.object(bench, 'discover', side_effect=ValueError('stop before inference')) as discover, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, bench.main())
                discover.assert_called_once_with('http://127.0.0.1:9010/v1')
                if response is None:
                    default.assert_not_called()
                    ask.assert_not_called()
                else:
                    self.assertIn('11234', ask.call_args.args[0])

    def test_readable_mlx_id_matches_local_tokenizer_without_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in ('Qwen3.8-27B-bf16', 'Qwen3.8-Flash-Next-MLX-Serve-8bit'):
                model = base / 'llm' / name
                model.mkdir(parents=True)
                (model / 'tokenizer.json').write_text('{}')
                with patch('builtins.input', side_effect=AssertionError('exact name needs no prompt')):
                    result = bench.resolve_tokenizer({'id': name}, base)
                self.assertEqual('local', result['kind'])
                self.assertEqual(str(model), result['value'])

    def test_unbuffered_child_and_environment(self):
        args = bench.command('/bin/uvx', bench.DEFAULT_URL, 'model', 'tokenizer', [0, 1, 2, 4], Path('/tmp/out.csv'))
        self.assertEqual('-u', args[args.index('python') + 1])
        self.assertEqual('1', bench.clean_environment(Path('/tmp/home'))['PYTHONUNBUFFERED'])

    def test_live_tail_preserves_raw_logs_and_filters_split_escapes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stdout.log'
            path.write_bytes(b'')
            tail = bench.LiveLog(path, 'stdout')
            chunks = [b'10%\r\x1b[', b'2J20%\x1b]52;c;secret', b'clipboard\x07done\x00\x08\n', '中文'.encode()[:2], '中文'.encode()[2:]]
            with contextlib.redirect_stdout(io.StringIO()) as out:
                for chunk in chunks:
                    with path.open('ab') as file:
                        file.write(chunk)
                    self.assertTrue(tail.drain())
                self.assertFalse(tail.drain())
            tail.close()
            self.assertEqual(b''.join(chunks), path.read_bytes())
            self.assertIn('10%', out.getvalue())
            self.assertIn('20%', out.getvalue())
            self.assertIn('中文', out.getvalue())
            for forbidden in ('\x1b', '\x07', '\x00', '\x08', 'secret', 'clipboard', '2J'):
                self.assertNotIn(forbidden, out.getvalue())

    def test_stderr_echoes_before_process_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Mock(returncode=1)
            process.poll.side_effect = [None, 1]
            output = io.StringIO()
            def launch(*args, **kwargs):
                kwargs['stderr'].write(b'dependency progress 50%\n')
                kwargs['stderr'].flush()
                return process
            def during_wait(_):
                self.assertIn('[stderr] dependency progress 50%', output.getvalue())
            with patch.object(bench.subprocess, 'Popen', side_effect=launch), patch.object(bench.time, 'sleep', side_effect=during_wait), contextlib.redirect_stdout(output):
                self.assertEqual(1, bench.run('/mock/uvx', bench.DEFAULT_URL, 'model', 'public/tokenizer', [0, 1, 2, 4], Path(directory) / 'reports'))
            raw = next((Path(directory) / 'reports').glob('llama-benchmark-*/stderr.log')).read_bytes()
            self.assertEqual(b'dependency progress 50%\n', raw)

    def test_silent_child_reports_elapsed_without_fake_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Mock(returncode=1)
            process.poll.side_effect = [None, 1]
            with patch.object(bench.subprocess, 'Popen', return_value=process), patch.object(bench.time, 'sleep'), patch.object(bench.time, 'monotonic', side_effect=[0, 11]), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(1, bench.run('/mock/uvx', bench.DEFAULT_URL, 'model', 'public/tokenizer', [0, 1, 2, 4], Path(directory) / 'reports'))
            self.assertIn('已运行 11 秒', out.getvalue())
            self.assertIn('等待测试进展（Ctrl-C 中断）', out.getvalue())
            self.assertNotIn('正在加载模型', out.getvalue())

    def test_interrupt_terminates_child_group_and_retains_logs(self):
        import signal
        with tempfile.TemporaryDirectory() as directory:
            process = Mock(pid=12345, returncode=None)
            process.poll.return_value = None
            with patch.object(bench.subprocess, 'Popen', return_value=process), patch.object(bench.time, 'sleep', side_effect=KeyboardInterrupt), patch.object(bench.os, 'killpg') as kill, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(130, bench.run('/mock/uvx', bench.DEFAULT_URL, 'model', 'public/tokenizer', [0, 1, 2, 4], Path(directory) / 'reports'))
            kill.assert_called_once_with(12345, signal.SIGTERM)
            process.wait.assert_called_once_with(timeout=5)
            self.assertEqual(1, len(list((Path(directory) / 'reports').glob('llama-benchmark-*/run.json'))))

    def test_menu_slot(self):
        source = (ROOT / 'start.sh').read_text()
        self.assertIn('5) benchmark_menu;', source)
        self.assertIn("ui_select 'Benchmark'", source)
        self.assertIn("'Llama benchmark' '0、50K、100K、200K 上下文吞吐测试；PP 计时不兼容 MLX-Serve、oMLX 等框架的空 role 消息。'", source)


if __name__ == '__main__':
    unittest.main()
