#!/usr/bin/env python3
"""仅标准库：通过 ddalcu/mlx-serve 测试 0、50K、100K、200K，各预热一次、正式测量一次。"""
import argparse
import csv
import datetime as dt
import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import random
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]


def default_report_directory():
    # UTC 时间戳与随机后缀分隔每次报告，默认不散落在调用目录。
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    return ROOT / 'output' / 'reports' / ('mlx-benchmark-' + stamp + '-' + uuid.uuid4().hex[:8])


DEPTHS = (0, 50000, 100000, 200000)
PP_TOKENS = 2048
OUTPUT_TOKENS = 480
TARGETS = tuple(depth + PP_TOKENS for depth in DEPTHS)
LABELS = {'unknown': '未知', 'clean': '无缓存', 'partial_cache': '少量缓存', 'cache_contaminated': '缓存污染',
          'success': '完成', 'error': '错误', 'interrupted': '中断', 'not_run': '未运行',
          'context_limit': '超上下文上限', 'invalid_metrics': '指标缺失', 'pending': '等待',
          'running': '运行中', 'preparing': '准备中'}
TIMING_FIELDS = ('prompt_n', 'cached_n', 'prompt_ms', 'prompt_per_second',
                 'predicted_n', 'predicted_ms', 'predicted_per_second', 'tokenize_ms')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class APIError(Exception):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status, self.body = status, body


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, timeout=1800):
        self.base_url = base_url.rstrip('/')
        parts = urllib.parse.urlsplit(self.base_url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.query or parts.fragment:
            raise ValueError('服务地址必须是无查询参数的 HTTP(S) URL。')
        if parts.username or parts.password:
            raise ValueError('不接受 URL 中的凭据；此脚本不使用 API key。')
        self.root = self.base_url[:-3] if self.base_url.endswith('/v1') else self.base_url
        if not self.base_url.endswith('/v1'):
            self.base_url += '/v1'
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, endpoint, payload=None, root=False):
        url = (self.root if root else self.base_url) + endpoint
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                body = response.read().decode('utf-8')
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            raise APIError('HTTP %s: %s' % (exc.code, body[:2000]), exc.code, body) from exc
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise APIError('服务响应不是有效 JSON。', body=body) from exc
        if not isinstance(value, dict):
            raise APIError('服务响应不是 JSON 对象。', body=body)
        return value


def choose_model(client, requested=None, input_fn=input):
    inventory = client.request('/models')
    models = [m['id'] for m in inventory.get('data', []) if isinstance(m, dict) and isinstance(m.get('id'), str)]
    models = list(dict.fromkeys(models))
    if not models:
        raise ValueError('/v1/models 未返回可用模型。')
    if requested:
        if requested not in models:
            raise ValueError('--model 必须与 /v1/models 中的模型 ID 完全一致。')
        return requested
    if len(models) == 1:
        return models[0]
    print('发现多个模型，请选择：')
    for index, model in enumerate(models, 1):
        print('  %d. %s' % (index, model))
    try:
        selection = int(input_fn('模型序号：'))
    except (ValueError, EOFError) as exc:
        raise ValueError('请选择有效序号，或通过 --model 指定模型。') from exc
    if not 1 <= selection <= len(models):
        raise ValueError('模型序号超出范围。')
    return models[selection - 1]


def corpus(target, nonce):
    """构造不同前缀和多样记录；不是内置应用语料的逐字复刻。"""
    rng = random.Random(nonce)
    prefix = nonce + '\n以下是测试记录。阅读后以连续编号简短列出 100 项具体观察，每项至少一句，持续输出直到达到输出上限，不要提前总结。\n'
    lines, size = [prefix], len(prefix)
    while size < target * 8 + 2048:
        line = 'Record %08d: station=%s value=%d balance=%d event=%s. Review the measurements and compare the records.\n' % (
            len(lines), rng.choice(('north', 'south', 'east', 'west')), rng.randrange(1000000),
            rng.randrange(1000000), rng.choice(('arrival', 'departure', 'inspection', 'adjustment')))
        lines.append(line)
        size += len(line)
    return ''.join(lines)


def prepare_prompt(client, model, target, nonce, transform=None):
    full = corpus(target, nonce)
    # 回退仅用明确标注的估计，不谎称知道 tokenizer 的真实计数。
    length = min(len(full), target * 4)
    best = None
    for attempt in range(8):
        text = full[:length]
        if transform:
            text = transform(text)
        try:
            response = client.request('/tokenize', {'content': text, 'model': model}, root=True)
            tokens = response.get('tokens')
            if not isinstance(tokens, list) or not tokens or not all(isinstance(t, int) and not isinstance(t, bool) for t in tokens):
                raise ValueError('/tokenize 未返回有效 tokens 数组。')
            count = len(tokens)
        except APIError as exc:
            if exc.status not in (404, 405, 501):
                raise
            return (transform(full[:target * 4]) if transform else full[:target * 4]), {'method': 'estimated_chars', 'prepared_content_tokens': None,
                'estimated_content_tokens': target, 'note': '/tokenize 不可用；按约 4 字符/token 估算，实际输入以服务返回为准。'}
        if best is None or abs(count - target) < abs(best[0] - target):
            best = (count, text)
        if abs(count - target) <= 8:
            break
        next_length = max(len(nonce) + 128, min(len(full), round(length * target / count)))
        if next_length == length:
            break
        length = next_length
    count, text = best
    return text, {'method': 'tokenize_content', 'prepared_content_tokens': count,
        'note': '/tokenize 计数不含完整聊天模板；真实输入以 timings.prompt_n（或 usage.prompt_tokens）为准。'}


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def parse_response(response, target, elapsed):
    timings = response.get('timings') or {}
    if not isinstance(timings, dict):
        timings = {}
    metrics = {field: number(timings.get(field)) for field in TIMING_FIELDS}
    usage = response.get('usage') or {}
    actual = metrics['prompt_n']
    if actual is None and isinstance(usage, dict):
        actual = number(usage.get('prompt_tokens'))
    a, b = metrics['tokenize_ms'], metrics['prompt_ms']
    metrics['estimated_server_ttft_ms'] = a + b if a is not None and b is not None else None
    metrics['client_elapsed_s'] = elapsed
    cached = metrics['cached_n']
    cache = 'unknown'
    if cached is not None and actual and actual > 0:
        cache = 'cache_contaminated' if cached / actual > 0.10 else ('partial_cache' if cached else 'clean')
    return actual, metrics, cache


class Heartbeat:
    def __init__(self, label, interval=10):
        self.label, self.interval = label, interval
        self.stop = threading.Event()
    def __enter__(self):
        def beat():
            start = time.monotonic()
            while not self.stop.wait(self.interval):
                print('%s：仍在等待（%.0f 秒），不重试。' % (self.label, time.monotonic() - start), flush=True)
        self.thread = threading.Thread(target=beat, daemon=True)
        self.thread.start()
        return self
    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=1)


def hardware_info(base_url):
    host = urllib.parse.urlsplit(base_url).hostname
    local = host == 'localhost'
    try:
        local = local or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if not local:
        return {'scope': 'unknown_remote', 'note': '远程服务硬件未知，未用客户端硬件冒充服务端。'}
    info = {'scope': 'local_client', 'os': platform.platform(), 'machine': platform.machine(), 'cpu_count': os.cpu_count(),
            'note': '本地客户端信息；容器、代理或端口转发时不一定代表实际推理服务器。'}
    try:
        info['physical_memory_bytes'] = os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    except (ValueError, OSError, AttributeError):
        pass
    return info


def save_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / 'results.json.tmp'
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(directory / 'results.json')
    fields = ['background_depth', 'pp_tokens', 'target_tokens', 'output_target', 'actual_output_tokens', 'output_status', 'finish_reason', 'status', 'actual_prompt_tokens', 'cache_status', 'token_accuracy_method', 'relative_error'] + list(TIMING_FIELDS) + ['estimated_server_ttft_ms', 'client_elapsed_s', 'error']
    with (directory / 'results.csv.tmp').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for result in report['results']:
            row = {key: result.get(key) for key in fields}
            row.update(result.get('metrics', {}))
            row['token_accuracy_method'] = result.get('token_accuracy', {}).get('method')
            row['relative_error'] = result.get('token_accuracy', {}).get('relative_error')
            for key, value in row.items():
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                    row[key] = "'" + value
            writer.writerow(row)
    (directory / 'results.csv.tmp').replace(directory / 'results.csv')
    try:
        from report_html import write_report
    except ImportError:
        return
    write_report(report, directory / 'report.html')


def run_benchmark(client, model, directory, hardware=False, note=''):
    report = {'schema_version': 2, 'metadata': {'started_at': now(), 'model': model, 'base_url': client.base_url,
              'note': note, 'hardware': hardware_info(client.base_url) if hardware else {'scope': 'not_collected'}},
              'config': {'targets': list(TARGETS), 'background_depths': list(DEPTHS), 'pp_tokens': PP_TOKENS, 'max_tokens': OUTPUT_TOKENS, 'warmup_generations': 1, 'warmup_scope': 'per_depth', 'initial_server_state': 'unknown', 'temperature': 0, 'top_p': 1,
              'enable_thinking': False, 'stream': False, 'timeout_s': client.timeout, 'repetitions': 1,
              'workload': '自定义多样文本；非内置语料逐字复刻', 'generation_retry': False},
              'results': [{'background_depth': depth, 'pp_tokens': PP_TOKENS, 'target_tokens': depth + PP_TOKENS, 'output_target': OUTPUT_TOKENS, 'status': 'pending', 'metrics': {}, 'error': None} for depth in DEPTHS]}
    # 预热单独归档，永不进入正式 CSV 或 HTML 汇总。
    report['warmups'] = [dict(row, metrics={}, phase='warmup') for row in report['results']]
    for row in report['results']:
        row['phase'] = 'measurement'
    jobs = [job for pair in zip(report['warmups'], report['results']) for job in pair]
    save_report(report, directory)
    current = None
    try:
        try:
            with Heartbeat('读取服务器配置'):
                report['metadata']['server_props'] = client.request('/props?model=' + urllib.parse.quote(model, safe=''), root=True)
        except Exception as exc:
            report['metadata']['server_props_error'] = str(exc)
        save_report(report, directory)
        props = report['metadata'].get('server_props', {})
        settings = props.get('default_generation_settings', {}) if isinstance(props, dict) else {}
        n_ctx = number(settings.get('n_ctx')) if isinstance(settings, dict) else None
        if not n_ctx:
            print('提示：服务器上下文上限未知；200K 可能超限或内存不足。', flush=True)
        for current in jobs:
            target = current['target_tokens']
            phase_label = '预热' if current['phase'] == 'warmup' else '正式测试'
            if n_ctx and target + OUTPUT_TOKENS + 512 > n_ctx:
                current.update(status='context_limit', error='已知上下文上限 %s 小于目标 + 输出 + 模板/检查余量（992）；未发送生成。' % n_ctx)
                save_report(report, directory)
                continue
            current.update(status='preparing', started_at=now())
            save_report(report, directory)
            print('\n%s %s tokens：准备输入。' % (phase_label, format(target, ',')), flush=True)
            try:
                nonce = uuid.uuid4().hex + uuid.uuid4().hex
                needles = ['N' + uuid.uuid4().hex[:16] for _ in range(3)]
                def add_checks(base):
                    pieces, offset = [], 0
                    for index, fraction in enumerate((0.23, 0.51, 0.79)):
                        position = int(len(base) * fraction)
                        pieces.extend([base[offset:position], '\nCHECKPOINT_%d=%s\n' % (index + 1, needles[index])])
                        offset = position
                    pieces.extend([base[offset:], '\n先返回三个 CHECKPOINT 的值，然后连续编号写出100项具体观察，每项至少一句，不要提前总结，持续至输出上限。'])
                    return ''.join(pieces)
                with Heartbeat('%s 分词校准' % target):
                    text, accuracy = prepare_prompt(client, model, target, nonce, transform=add_checks)
                if accuracy['method'] == 'tokenize_content':
                    with Heartbeat('%s 最终分词计数' % target):
                        final_tokens = client.request('/tokenize', {'content': text, 'model': model}, root=True).get('tokens')
                    if not isinstance(final_tokens, list) or not final_tokens or not all(isinstance(t, int) and not isinstance(t, bool) for t in final_tokens):
                        raise ValueError('最终 /tokenize 响应无有效 tokens 数组。')
                    accuracy['prepared_content_tokens'] = len(final_tokens)
                prepared_count = accuracy.get('prepared_content_tokens')
                if n_ctx and prepared_count and prepared_count + OUTPUT_TOKENS + 512 > n_ctx:
                    current.update(status='context_limit', token_accuracy=accuracy, error='最终分词计数 + 输出与模板余量超过上下文上限，未发送生成。')
                    continue
                current['context_check'] = {'expected': needles, 'passed': None, 'note': '三个位置的字符串检索检查，不等同全面长上下文能力评测。'}
                current['token_accuracy'] = accuracy
                payload = {'model': model, 'messages': [{'role': 'user', 'content': text}], 'stream': False,
                           'temperature': 0, 'top_p': 1, 'max_tokens': OUTPUT_TOKENS, 'enable_thinking': False}
                current.update(request=payload, prompt_sha256=hashlib.sha256(text.encode()).hexdigest(), status='running')
                save_report(report, directory)
                print('%s：%s（1/1）。' % (target, phase_label), flush=True)
                started = time.monotonic()
                try:
                    with Heartbeat('%s %s' % (target, phase_label)):
                        response = client.request('/chat/completions', payload)
                finally:
                    current.setdefault('metrics', {})['client_elapsed_s'] = time.monotonic() - started
                current['response'] = response
                if 'error' in response:
                    raise APIError('服务返回 error：' + json.dumps(response['error'], ensure_ascii=False), body=response)
                choices = response.get('choices', [])
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict) or not isinstance(choices[0].get('message'), dict) or not isinstance(choices[0]['message'].get('content'), str):
                    raise APIError('生成响应缺少有效 choices/message/content；执行结果不完整，不继续后续请求。', body=response)
                answer = choices[0].get('message', {}).get('content', '') if choices and isinstance(choices[0], dict) else ''
                current['context_check']['passed'] = all(value in (answer or '') for value in needles)
                current['context_check']['found'] = [value in (answer or '') for value in needles]
                if 'error' in response:
                    raise APIError('服务返回 error：' + json.dumps(response['error'], ensure_ascii=False), body=response)
                actual, metrics, cache = parse_response(response, target, time.monotonic() - started)
                current.update(actual_prompt_tokens=actual, metrics=metrics, cache_status=cache, status='success',
                    actual_output_tokens=metrics['predicted_n'], finish_reason=choices[0].get('finish_reason'),
                    output_status=('unknown' if metrics['predicted_n'] is None else 'target_reached' if metrics['predicted_n'] == OUTPUT_TOKENS else 'short_output' if metrics['predicted_n'] < OUTPUT_TOKENS else 'over_target'),
                    metric_quality='complete' if all(metrics[k] is not None for k in TIMING_FIELDS) else 'missing_timings',
                    pp_valid=cache == 'clean' and metrics['prompt_per_second'] is not None)
                if current['metric_quality'] != 'complete':
                    current['status'] = 'invalid_metrics'
                    current['error'] = '请求完成，但缺少完整原生 timings，不能视为有效性能测量。'
                if current['phase'] == 'warmup' and current['status'] != 'success':
                    report['metadata']['stop_reason'] = '预热指标不完整；停止正式测试与后续档位，不重试。'
                    break
                accuracy['relative_error'] = (actual - target) / target if actual is not None else None
                accuracy['status'] = 'unknown' if actual is None else ('near_target' if abs(actual - target) / target <= 0.01 else 'off_target')
                print('%s 完成；实际输入=%s；缓存=%s' % (target, actual if actual is not None else '未知', LABELS.get(cache, cache)), flush=True)
            except APIError as exc:
                current.update(status='error', error=str(exc), error_http_status=exc.status, error_body=exc.body)
                if current['phase'] == 'warmup':
                    report['metadata']['stop_reason'] = '预热失败；停止正式测试与后续档位，不重试。'
                    break
                if (exc.status is None or exc.status in (502, 503, 504)) and current.get('request') is not None:
                    report['metadata']['stop_reason'] = '生成响应无效，执行情况不确定；停止后续档位，不重试。'
                    break
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, http.client.HTTPException) as exc:
                current.update(status='error', error=str(exc), execution_state='unknown_if_request_was_sent')
                report['metadata']['stop_reason'] = '连接中断或超时；服务器可能仍在运行，为避免重叠，不继续后续档位，也不重试。'
                break
            except Exception as exc:
                current.update(status='error', error='%s: %s' % (type(exc).__name__, exc))
                if current['phase'] == 'warmup' or current.get('request') is not None:
                    current['execution_state'] = 'unknown_if_request_was_sent'
                    report['metadata']['stop_reason'] = '生成阶段发生未预期错误；停止后续档位，不重试。'
                    break
            finally:
                current['finished_at'] = now()
                save_report(report, directory)
    except KeyboardInterrupt:
        if current is not None:
            current.update(status='interrupted', error='用户中断；若已发送请求，服务器可能仍在运行。', finished_at=now())
        report['metadata']['stop_reason'] = '用户中断，不重试，不继续。'
        print('\n已中断，保留已完成结果。服务器可能需要手动停止当前生成。', flush=True)
    finally:
        for row in jobs:
            if row['status'] == 'pending':
                row['status'] = 'not_run'
        report['metadata']['finished_at'] = now()
        save_report(report, directory)
    return report


class ChineseHelpFormatter(argparse.HelpFormatter):
    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(usage, actions, groups, prefix or '用法：')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, add_help=False, formatter_class=ChineseHelpFormatter)
    parser._positionals.title = '位置参数'
    parser._optionals.title = '选项'
    parser.add_argument('-h', '--help', action='help', help='显示帮助并退出')
    parser.add_argument('--base-url', default=None, help='MLX-Serve API 地址（默认 http://127.0.0.1:11234/v1，不使用代理配置）')
    parser.add_argument('--root', default=str(Path.home() / 'AI'), help='兼容工具包参数；不用于选择 API 地址')
    parser.add_argument('--models-dir', type=str, default=str(Path.home() / 'AI/models'), help='兼容工具包模型目录参数；不用于选择 API 地址')
    parser.add_argument('--model', help='精确模型 ID；省略时自动选择唯一模型或交互选择')
    parser.add_argument('--timeout', type=float, default=1800, help='每次 HTTP 操作超时秒数；200K 可设 3600，默认 1800')
    parser.add_argument('--output', help='本次报告目录；默认项目内 output/reports/mlx-benchmark-时间戳-随机后缀')
    parser.add_argument('--note', default='', help='报告备注，例如 mtp=5；仅记录，不修改服务器设置')
    parser.add_argument('--hardware', action='store_true', help='记录本机基础信息，仅本地服务地址适用')
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout 必须为有限正数。')
    directory = Path(args.output).expanduser() if args.output is not None else default_report_directory()
    if (directory / 'results.json').exists():
        parser.error('输出目录已有 results.json，请指定新目录，避免覆盖。')
    try:
        base_url = args.base_url
        if base_url is None:
            default = 'http://127.0.0.1:11234/v1'
            base_url = input('MLX-Serve API 地址 [%s]：' % default).strip() or default
        client = Client(base_url, args.timeout)
        with Heartbeat('查询/选择模型'):
            model = choose_model(client, args.model)
        print('模型：%s\n背景 0 / 50K / 100K / 200K + 输入 2048；每档预热 1 次、正式 1 次。' % model)
        print('接口：%s\n输出上限：每档 480 tokens（可能提前结束）\n报告目录：%s' % (client.base_url, directory.resolve()))
        report = run_benchmark(client, model, directory, args.hardware, args.note)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        print('无法连接 MLX-Serve API：%s' % base_url, file=sys.stderr)
        print('请在 MLX-Serve App 中开启 API，并按 App 显示的端口填写地址；无需启动 llama-swap。', file=sys.stderr)
        print('连接详情：%s' % exc, file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt) as exc:
        print('无法开始测试：%s' % exc, file=sys.stderr)
        return 1
    print('\n目标输入 / 实际输入 / 实际输出 / PP(tok/s) / TG(tok/s) / 首 token 估算(ms) / 缓存 / 验证 / 状态')
    def display(value):
        return '未知' if value is None else ('%.2f' % value if isinstance(value, float) else str(value))
    for row in report['results']:
        metrics = row.get('metrics', {})
        check = row.get('context_check', {}).get('passed')
        print(' / '.join([str(row['target_tokens']), display(row.get('actual_prompt_tokens')), display(row.get('actual_output_tokens')),
            display(metrics.get('prompt_per_second')), display(metrics.get('predicted_per_second')),
            display(metrics.get('estimated_server_ttft_ms')), LABELS.get(row.get('cache_status', 'unknown'), '未知'),
            '未知' if check is None else ('通过' if check else '未通过'), LABELS.get(row['status'], row['status'])]))
    print('首 token 为服务端 tokenize_ms + prompt_ms 估算，不是真实网络 TTFT。')
    print('报告已保存：%s' % directory.resolve())
    for filename in ('report.html', 'results.csv', 'results.json'):
        if (directory / filename).exists():
            print('  ' + str((directory / filename).resolve()))
    return 0 if all(row['status'] == 'success' for row in report['results']) else 1


if __name__ == '__main__':
    sys.exit(main())
