"""将 Llama Benchy 原 CSV 映射到共用离线报告，不改动原始数据。"""

from __future__ import annotations
import math
import re
from pathlib import Path
from report_html import write_report


def _number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and n >= 0 else None
    except (ValueError, TypeError):
        return None


def build_report(headers, rows, metadata, *, failed=False, warning=''):
    required = {'model', 'test_name', 't_s_mean', 'e2e_ttft_mean'}
    if not required.issubset(headers):
        raise ValueError('CSV 表头不符合 Llama Benchy 0.4.0')
    grouped = {}
    for row in rows:
        item = dict(zip(headers, row))
        if item['model'] != metadata['model']:
            raise ValueError('CSV 模型名称与本次请求不一致')
        match = re.fullmatch(r'(pp|tg)(\d+)(?: @ d(\d+))?', item['test_name'])
        if not match:
            raise ValueError('CSV 包含无法识别的测试名称')
        kind, size, depth = match.group(1), int(match.group(2)), int(match.group(3) or 0)
        if size != metadata[kind] or depth not in metadata['depths']:
            raise ValueError('CSV 测试档位与本次请求不一致')
        if kind in grouped.setdefault(depth, {}):
            raise ValueError('CSV 含重复测试记录')
        grouped[depth][kind] = item
    runs = []
    for depth in metadata['depths']:
        pair = grouped.get(depth, {})
        pp, tg = pair.get('pp', {}), pair.get('tg', {})
        valid = len(pair) == 2 and all(_number(x.get('t_s_mean')) not in (None, 0) for x in (pp, tg))
        runs.append(
            {
                'target_tokens': depth + metadata['pp'],
                'background_depth': depth,
                'actual_prompt_tokens': None,
                'predicted_n': None,
                'prompt_per_second': _number(pp.get('t_s_mean')),
                'predicted_per_second': _number(tg.get('t_s_mean')),
                'client_e2e_ttft_ms': _number(pp.get('e2e_ttft_mean')),
                'status': 'success' if valid and not failed else 'error' if pair else 'not_run',
                'measurement_source': 'llama_benchy_client',
                'measurement_valid': valid and not failed,
                'cache_status': 'unknown',
                'raw_csv_rows': pair,
            }
        )
    return {
        'measurement_source': 'llama_benchy_client',
        'model': metadata['model'],
        'generated_at': metadata.get('created_utc'),
        'config': {
            **metadata,
            'warmup_generations_per_depth': 1,
            'warmup_note': 'llama-benchy 0.4.0 默认每档另预热一次；另有模板校准和延迟探针',
        },
        'config_source': '本次 run.json 与原始 CSV；客户端计时',
        'runs': runs,
        'report_warning': warning or ('测试未完成；部分原始结果仅供排查，不纳入摘要。' if failed else ''),
    }


def save_report(folder: Path, headers, rows, metadata, *, failed=False, warning=''):
    return write_report(build_report(headers, rows, metadata, failed=failed, warning=warning), folder / 'report.html')
