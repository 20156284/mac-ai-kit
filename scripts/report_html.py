"""四档 MLX 基准的独立离线 HTML 报告，仅用标准库。
仅显示实际返回的指标；缺失值不推算，接口文本全部转义。"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) and n >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _fmt(value: Any, digits: int = 1, suffix: str = '') -> str:
    n = _number(value)
    return '—' if n is None else f'{n:,.{digits}f}{suffix}'


def _get(run: dict, *keys: str) -> Any:
    for key in keys:
        if key in run and run[key] is not None:
            return run[key]
    return None


def _metric(run: dict, name: str) -> Any:
    aliases = {
        'target': ('target_tokens', 'target_prompt_tokens', 'target'),
        'actual': ('actual_prompt_tokens', 'prompt_tokens', 'input_tokens'),
        'pp': ('prompt_per_second', 'pp_tokens_per_second', 'prompt_tps', 'pp_tps', 'prompt_tokens_per_second'),
        'tg': (
            'predicted_per_second',
            'tg_tokens_per_second',
            'generation_tps',
            'tg_tps',
            'generation_tokens_per_second',
        ),
        'ttft': (
            'client_e2e_ttft_ms',
            'estimated_server_ttft_ms',
            'estimated_ttft_ms',
            'ttft_estimate_ms',
            'ttft_estimated_ms',
        ),
        'cached': ('cached_n', 'cached_tokens', 'prompt_cached_tokens'),
        'generated': ('predicted_n', 'generated_tokens', 'completion_tokens'),
        'memory': ('peak_memory_gb', 'memory_gb'),
    }
    metrics = run.get('metrics', {})
    metrics = metrics if isinstance(metrics, dict) else {}
    value = _get(metrics, *aliases[name])
    if value is None:
        value = _get(run, *aliases[name])
    if name == 'ttft' and value is None:
        prompt = _number(metrics.get('prompt_ms', run.get('prompt_ms')))
        tokenize = _number(metrics.get('tokenize_ms', run.get('tokenize_ms')))
        if prompt is not None and tokenize is not None:
            return prompt + tokenize
    return value


def _eligible(run: dict, metric: str) -> bool:
    if run.get('status') not in (None, 'success') or run.get('error'):
        return False
    if run.get('measurement_source') == 'llama_benchy_client':
        # 客户端 PP 是推导估计，不放入原生 PP 摘要；TG 保留原始工具口径。
        return metric != 'pp' and run.get('measurement_valid') is not False
    if metric == 'pp':
        metrics = run.get('metrics', {})
        return (
            run.get('cache_status') == 'clean'
            and _number(_metric(run, 'cached')) == 0
            and run.get('pp_valid') is not False
            and (not isinstance(metrics, dict) or metrics.get('pp_valid') is not False)
        )
    if metric == 'tg' and run.get('output_status') in ('short_output', 'over_target', 'unknown'):
        return False
    return True


def _chart(runs: list[dict], metric: str, title: str, color: str) -> str:
    points = []
    for run in runs:
        if not _eligible(run, metric):
            continue
        x, y = _number(_metric(run, 'target')), _number(_metric(run, metric))
        if x is not None and y is not None:
            points.append((x, y))
    points.sort()
    if not points:
        return f'<section class="panel"><h2>{_esc(title)}</h2><div class="empty">尚无有效实测数据</div><p class="hint">缺失、失败或服务未返回的指标不绘制。</p></section>'
    left, top, width, height = 66, 35, 440, 166
    all_x = [_number(_metric(r, 'target')) for r in runs]
    all_x = sorted(set(x for x in all_x if x is not None)) or [p[0] for p in points]
    xmin, xmax = min(all_x), max(all_x)
    ymax = max(y for _, y in points) * 1.2 or 1

    def px(x):
        return left + width * (x - xmin) / (xmax - xmin) if xmax > xmin else left + width / 2

    def py(y):
        return top + height * (1 - y / ymax)

    svg = [
        f'<svg viewBox="0 0 550 255" role="img" aria-label="{_esc(title)}，横轴为目标 prompt token 数，纵轴为 token 每秒"><title>{_esc(title)}</title>'
    ]
    for i in range(5):
        y = top + height * i / 4
        svg.append(
            f'<line x1="{left}" y1="{y}" x2="{left + width}" y2="{y}" stroke="#e7edf4"/><text x="{left - 10}" y="{y + 4}" text-anchor="end" class="axis">{_fmt(ymax * (1 - i / 4), 0)}</text>'
        )
    for x in all_x:
        svg.append(f'<text x="{px(x):.2f}" y="229" text-anchor="middle" class="axis">{_fmt(x / 1000, 0)}K</text>')
    # 缺失点中断连线，避免暗示该档已成功测量。
    lookup = dict(points)
    for a, b in zip(all_x, all_x[1:]):
        if a in lookup and b in lookup:
            svg.append(
                f'<line x1="{px(a):.2f}" y1="{py(lookup[a]):.2f}" x2="{px(b):.2f}" y2="{py(lookup[b]):.2f}" stroke="{color}" stroke-width="3"/>'
            )
    for x, y in points:
        svg.append(
            f'<circle cx="{px(x):.2f}" cy="{py(y):.2f}" r="5" fill="{color}" stroke="white" stroke-width="2"/><text x="{px(x):.2f}" y="{py(y) - 13:.2f}" text-anchor="middle" class="point">{_fmt(y)}</text>'
        )
    svg.append('</svg>')
    return f'<section class="panel"><h2>{_esc(title)} <span>tok/s</span></h2>{"".join(svg)}<p class="hint">横轴：目标 prompt tokens。每档一次，不表示稳定性或性能上限。</p></section>'


def render_report(report: dict[str, Any]) -> str:
    """将基准结果转换为可离线打开的 UTF-8 HTML。"""
    runs = report.get('runs', report.get('results', []))
    runs = [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else []
    metadata = report.get('metadata', {})
    metadata = metadata if isinstance(metadata, dict) else {}
    config = report.get('config', {})
    props = metadata.get('server_props', report.get('server_props', {}))
    props = props if isinstance(props, dict) else {}
    model = report.get('model') or metadata.get('model') or '未知模型'
    hardware = report.get('hardware') or metadata.get('hardware') or '未知：服务未提供硬件信息'
    if isinstance(hardware, dict):
        hardware = (
            '未采集硬件'
            if hardware.get('scope') == 'not_collected'
            else ' · '.join(f'{k}: {v}' for k, v in hardware.items()) or '未知'
        )

    def meta(key, fallback='未知 / 未返回'):
        aliases = {
            'kv_cache': ('kv_quant', 'kv_cache', 'kv_cache_type', 'cache_type_k'),
            'attention': ('kv_attn_mode', 'attention', 'attention_backend', 'flash_attn'),
            'pld': ('pld', 'pld_enabled'),
            'mtp': ('mtp', 'mtp_enabled'),
            'context_length': ('context_length', 'n_ctx', 'max_model_len'),
        }
        for obj in (
            report,
            metadata,
            props,
            props.get('settings', {}),
            props.get('default_generation_settings', {}),
            config,
        ):
            if isinstance(obj, dict):
                for alias in aliases.get(key, (key,)):
                    if alias in obj and obj[alias] is not None:
                        value = obj[alias]
                        if isinstance(value, dict):
                            if key in ('mtp', 'pld'):
                                parts = []
                                if 'loaded' in value:
                                    parts.append('已加载' if value['loaded'] else '未加载')
                                if 'default_on' in value:
                                    parts.append('默认开启' if value['default_on'] else '默认关闭')
                                if value.get('adaptive') is not None:
                                    parts.append('自适应开启' if value['adaptive'] else '自适应关闭')
                                for field, label in (
                                    ('depth', '深度'),
                                    ('draft_len', '草稿长度'),
                                    ('max_ctx', '上下文阈值'),
                                ):
                                    if value.get(field) is not None:
                                        parts.append(f'{label} {value[field]}')
                                return ' · '.join(parts) or fallback
                            return fallback
                        if isinstance(value, bool):
                            return '开启' if value else '关闭'
                        return value
        return fallback

    def best(metric, minimum=False):
        values = [_number(_metric(r, metric)) for r in runs if _eligible(r, metric)]
        values = [v for v in values if v is not None]
        return (min(values) if minimum else max(values)) if values else None

    chips = [
        ('硬件', hardware),
        ('KV cache', meta('kv_cache')),
        ('Attention', meta('attention')),
        ('PLD', meta('pld')),
        ('MTP', meta('mtp')),
        ('Context', meta('context_length')),
    ]
    chips_html = ''.join(f'<div class="chip"><span>{_esc(k)}</span><strong>{_esc(v)}</strong></div>' for k, v in chips)
    rows = []
    for r in runs:
        error = r.get('error')
        status = r.get('status') or ('失败' if error else '已记录')
        status = {
            'pending': '待开始',
            'preparing': '准备中',
            'running': '运行中',
            'success': '成功',
            'error': '失败',
            'interrupted': '已中断',
            'not_run': '未运行',
        }.get(status, status)
        cache_key = r.get('cache_status', 'unknown')
        cache = {
            'unknown': '未知',
            'clean': '无缓存命中',
            'partial_cache': '部分命中，PP 不纳入摘要',
            'cache_contaminated': '警告：缓存污染，PP 不纳入摘要',
        }.get(cache_key, cache_key)
        check = r.get('context_check', {})
        passed = check.get('passed') if isinstance(check, dict) else None
        check_label = '通过' if passed is True else '未通过' if passed is False else '未测'
        accuracy = r.get('token_accuracy', {})
        accuracy_note = accuracy.get('note') if isinstance(accuracy, dict) else None
        output_note = {
            'short_output': '输出不足目标，TG 不纳入摘要',
            'target_reached': '达到输出目标',
            'unknown': '实际输出未知，TG 不纳入摘要',
            'over_target': '输出超过目标，TG 不纳入摘要',
        }.get(r.get('output_status'), '输出目标未记录')
        output_note += '；结束原因：' + str(r.get('finish_reason') or '未返回')
        cells = [
            _fmt(_metric(r, 'target'), 0),
            _fmt(_metric(r, 'actual'), 0),
            _fmt(_metric(r, 'pp')),
            _fmt(_metric(r, 'tg')),
            _fmt((_number(_metric(r, 'ttft')) / 1000) if _number(_metric(r, 'ttft')) is not None else None, 2),
            _fmt(_metric(r, 'cached'), 0),
            _fmt(_metric(r, 'generated'), 0),
            _fmt(_metric(r, 'memory'), 2),
            check_label,
        ]
        rows.append(
            '<tr>'
            + ''.join(f'<td>{v}</td>' for v in cells)
            + f'<td class="status">{_esc(status)}<small>缓存：{_esc(cache)}</small><small>{_esc(output_note)}</small>'
            + (f'<small>{_esc(accuracy_note)}</small>' if accuracy_note else '')
            + (f'<small>{_esc(error)}</small>' if error else '')
            + '</td></tr>'
        )
    if not rows:
        rows = ['<tr><td colspan="10" class="empty">没有运行记录。未生成任何性能数值。</td></tr>']
    source = report.get('config_source', metadata.get('config_source', '未记录'))
    config_data = {'config_source': source, 'config': config, 'server_props': props, 'metadata': metadata}
    config_json = _esc(json.dumps(config_data, ensure_ascii=False, indent=2, default=str))
    ttft = best('ttft', minimum=True)
    generated_at = report.get(
        'generated_at', metadata.get('finished_at') or metadata.get('started_at') or report.get('started_at', '未记录')
    )
    page = (
        '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MLX-Serve 长上下文 benchmark</title><style>
:root{color-scheme:light;--ink:#14243b;--muted:#65758a;--blue:#2469df;--orange:#ea8730}*{box-sizing:border-box}body{margin:0;background:#f4f7fb;color:var(--ink);font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;font-size:14px;line-height:1.55}main{max-width:1200px;margin:auto;padding:44px 28px 32px}.eyebrow{color:var(--blue);font-size:11px;font-weight:800;letter-spacing:2px}h1{font-size:34px;line-height:1.2;margin:10px 0 12px;overflow-wrap:anywhere}h2{font-size:16px;margin:0 0 20px}h2 span{float:right;color:var(--muted);font-size:12px;font-weight:400}.subtitle,.hint{color:var(--muted)}.subtitle{margin:0 0 22px}.hint{font-size:12px;margin:12px 0 0}.chips{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:26px}.chip{padding:8px 12px;border:1px solid #e2e9f1;background:white;border-radius:8px;max-width:100%;overflow-wrap:anywhere}.chip span{color:var(--muted);margin-right:8px;font-size:11px}.chip strong{font-size:12px;font-weight:600}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}.card,.panel{background:white;border:1px solid #e2e9f1;border-radius:14px;padding:23px}.card{border-top:3px solid var(--blue)}.card.orange{border-top-color:var(--orange)}.card label{color:var(--muted);font-size:12px}.value{font-weight:750;font-size:38px;line-height:1.3;letter-spacing:-1px;margin:10px 0}.value span{font-size:14px;font-weight:400;color:var(--muted);letter-spacing:0}.card p{color:var(--muted);font-size:11px;margin:0}.charts{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}svg{width:100%;display:block}.axis{fill:#738298;font-size:11px}.point{fill:#344761;font-size:12px;font-weight:600}.notice{padding:15px 18px;background:#fff8ef;border:1px solid #f2dcc0;border-radius:10px;color:#805322;font-size:12px}.table-panel{margin:20px 0;padding:23px 0}.table-panel h2,.table-panel .hint{padding:0 23px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:12px}th{text-align:right;font-size:10px;color:var(--muted);background:#f8fafd;font-weight:600;padding:14px 12px;border-bottom:1px solid #e2e9f1}td{text-align:right;padding:17px 12px;border-bottom:1px solid #edf1f6;font-variant-numeric:tabular-nums}th:first-child,td:first-child{text-align:left;padding-left:23px}td.status{text-align:left;white-space:normal;min-width:110px;max-width:250px}small{display:block;color:#a35b22;overflow-wrap:anywhere;margin-top:5px}.empty{text-align:center;color:var(--muted);padding:65px 15px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;background:#f8fafd;padding:16px;border-radius:8px;color:#40546e}summary{cursor:pointer;font-weight:600}footer{font-size:11px;color:var(--muted);padding:24px 0} @media(max-width:760px){main{padding:25px 14px}h1{font-size:27px}.cards,.charts{grid-template-columns:1fr}.value{font-size:32px}.card,.panel{padding:18px}.table-panel{padding:18px 0}} @media print{body{background:white}main{max-width:none;padding:10px}.card,.panel{break-inside:avoid}details{display:block}}
</style></head><body><main>
<div class="eyebrow">MLX · 长上下文基准</div>
'''
        + f'''<h1>{_esc(model)}</h1><p class="subtitle">背景 50K / 100K / 200K + 输入 2048 · 输出目标 480 · 每档一次</p><p class="hint">配置快照：默认配置不等于逐次请求实际启用状态，MTP 可能受上下文阈值限制。</p><div class="chips">{chips_html}</div>
<div class="notice">本报告反映四档正式测试：背景 0 / 50K / 100K / 200K，每档先生成预热 1 次、再正式测量 1 次。预热不进入成绩；每次使用不同前缀，仍需查看实际缓存命中。单次采样不提供标准差、跨轮漂移或性能上限。TTFT 为服务端估算，不是客户端端到端首 token 延迟；“—”表示未测得或接口未提供。</div>
<div class="cards"><section class="card"><label>最佳生成吞吐 · TG</label><div class="value">{_fmt(best('tg'))} <span>tok/s</span></div><p>仅达到输出目标的有效样本；不足目标的原始值保留在表中，不是性能上限</p></section><section class="card orange"><label>最佳提示词处理吞吐 · PP</label><div class="value">{_fmt(best('pp'))} <span>tok/s</span></div><p>仅成功且确认无缓存命中的样本；原始值见下表</p></section><section class="card"><label>最低服务端估算 · TTFT</label><div class="value">{_fmt(ttft / 1000 if ttft is not None else None, 2)} <span>s</span></div><p>prompt_ms + tokenize_ms，不含网络或排队等开销</p></section></div>
<div class="charts">{_chart(runs, 'tg', '生成吞吐 · TG', '#2469df')}{_chart(runs, 'pp', '提示词处理吞吐 · PP', '#ea8730')}</div>
<section class="panel table-panel"><h2>逐档测试结果</h2><div class="scroll"><table><thead><tr><th>目标 tokens</th><th>实际 prompt</th><th>PP · tok/s</th><th>TG · tok/s</th><th>TTFT 估算 · s</th><th>缓存 tokens</th><th>生成 tokens</th><th>峰值内存 · GB</th><th>上下文验证</th><th>状态</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><p class="hint">横轴与目标档位为十进制 tokens（2,048 / 52,048 / 102,048 / 202,048），即背景深度加 2,048；聊天模板可能增加少量 tokens。以实际 prompt token 数核对长度；缓存与内存仅显示服务返回值。零缓存与未知缓存严格区分。</p></section>
<section class="panel"><h2>方法与数据边界</h2><p class="hint">PP 与 TG 来自接口实测字段。目标长度与实际长度可能不同，请查阅逐档结果。缓存命中会影响 prompt 处理时间，不能把缓存后的吞吐当作冷启动吞吐。PP 最佳值与曲线仅纳入 cache_status=clean 、cached_n=0 且未标记无效的成功样本；其余原始 PP 值保留在表中。所有摘要与曲线排除失败样本；TG 摘要与曲线还排除输出不足或超出目标的样本。逐档峰值内存仅在该次响应明确返回时显示；接口未返回则为未测量，不用服务全局历史峰值代替。</p><p class="hint">本脚本对齐 Llama Benchy 的四档输入预算、输出目标与每档预热/正式次数，不复刻其语料或客户端计时。原生 MLX-Serve 套件包含预热与重复采样，本报告不是该套件的等价扩档。标准差：未测量 · 跨轮漂移：未测量 · 性能上限：未测量。未提供指标不使用推测值补齐。</p></section>
<section class="panel" style="margin-top:20px"><details><summary>配置与来源 · 查看原始记录</summary><pre>{config_json}</pre></details></section><footer>生成时间：{_esc(generated_at)} · 自包含离线 HTML · 无外部脚本、字体或网络资源</footer></main></body></html>'''
    )
    if report.get('measurement_source') == 'llama_benchy_client':
        # 复用同一版式，指标标签与方法说明保持独立，绝不伪装成服务端计时。
        page = page.replace('MLX-Serve 长上下文 benchmark', 'Llama Benchy 基准报告').replace(
            'MLX · 长上下文基准', 'LLAMA BENCHY · 客户端基准'
        )
        start = page.index('<p class="subtitle">')
        end = page.index('</p>', start) + 4
        depths = ' / '.join(str(x) for x in config.get('depths', []))
        page = (
            page[:start]
            + f'<p class="subtitle">背景 {_esc(depths)} + 输入 {_esc(config.get("pp", 2048))} · 输出目标 {_esc(config.get("tg", 480))} · 正式每档 {_esc(config.get("runs", 1))} 次</p>'
            + page[end:]
        )
        start = page.index('<div class="notice">')
        end = page.index('</div>', start) + 6
        page = (
            page[:start]
            + '<div class="notice">Llama Benchy 客户端流式计时；不是 MLX 服务端原生计时。PP 为客户端推导值，空 role 首包可能使其虚高，原值仅留表格，不纳入 PP 摘要或曲线。缓存、实际 token 数和结束原因若 CSV 未提供则保持未知；目标长度不是实际长度。</div>'
            + page[end:]
        )
        page = (
            page.replace('最低服务端估算 · TTFT', '最低客户端端到端 · TTFT')
            .replace('TTFT 估算 · s', '客户端 TTFT · s')
            .replace(
                'prompt_ms + tokenize_ms，不含网络或排队等开销',
                'CSV e2e_ttft_mean，包含客户端等待；不可与服务端估算等同',
            )
        )
        page = page.replace('最佳提示词处理吞吐 · PP', '服务端原生 PP · 未提供').replace(
            '仅成功且确认无缓存命中的样本；原始值见下表', '客户端 PP 原值仅见表格，不作为服务端 PP 排名'
        )
        page = page.replace(
            '仅达到输出目标的有效样本；不足目标的原始值保留在表中，不是性能上限',
            '原工具 TG 均值；实际输出数量未知，不等于已核实达到目标',
        )
        page = page.replace('提示词处理吞吐 · PP', '客户端 PP · 仅原始表格').replace(
            '<th>PP · tok/s</th>', '<th>客户端 PP 估算 · tok/s</th>'
        )
        page = page.replace('输出目标未记录；结束原因：未返回', 'CSV 未提供实际输出与结束原因').replace(
            '缓存：未知', '缓存：未提供'
        )
        start = page.index('<p class="hint">横轴与目标档位')
        end = page.index('</p>', start) + 4
        page = (
            page[:start]
            + '<p class="hint">目标 tokens 为背景深度 + pp；实际输入、生成数不由测试名称反推。原始 CSV、stdout.log、stderr.log 保留；部分或失败结果不冒充完整测试。</p>'
            + page[end:]
        )
        start = page.index('<section class="panel"><h2>方法与数据边界</h2>')
        end = page.index('</section>', start) + len('</section>')
        page = (
            page[:start]
            + '<section class="panel"><h2>方法与数据边界</h2><p class="hint">从原始 CSV 的 test_name 配对 PP/TG；t_s_mean 与 e2e_ttft_mean 原值读取，不重新推算纯计算吞吐。TG 与端到端首 token 使用客户端口径，PP 可能受空流式消息及延迟扣除影响。未采集逐条 SSE，不能仅凭 CSV 证明已修正计时。</p><p class="hint">工具内部准备请求不等于本报告新增预热；具体原命令见配置。标准差仅在原 CSV 中保留，单次的 0 不代表无波动。缓存、Ceiling、Drift、实际输出、结束原因：未提供。与 MLX 报告仅版式统一，不代表测量方法等价。</p></section>'
            + page[end:]
        )
        if report.get('report_warning'):
            page = page.replace(
                '<div class="cards">',
                '<div class="notice">' + _esc(report['report_warning']) + '</div><div class="cards">',
                1,
            )
    return page


def write_report(report: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(report), encoding='utf-8')
    return path
