#!/usr/bin/env python3
"""离线检查已知文件；只维护 API.md 的标记区，不执行程序或联网。"""
import argparse
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BEGIN = '<!-- MAC-AI-KIT:API:BEGIN -->'
END = '<!-- MAC-AI-KIT:API:END -->'


def safe_root(value):
    path = Path(value).expanduser().absolute()
    if path == Path('/') or '..' in path.parts or any(c in str(path) for c in '\n\r'):
        raise ValueError('目录无效')
    # 系统 /tmp、/var 在 macOS 本身可能是链接；只拒绝指定目录及非系统父级链接。
    for p in (path, *path.parents):
        if p.is_symlink() and str(p) not in ('/tmp', '/var', '/etc'):
            raise ValueError('目录或父目录是符号链接，请指定真实目录')
    if not path.is_dir():
        raise ValueError('目录不存在；检查不会创建安装目录')
    return path.resolve()


def relative_path(base, value):
    rel = Path(value)
    if rel.is_absolute() or not rel.parts or '..' in rel.parts:
        raise ValueError('清单路径越界')
    return base / rel


def no_link_parents(path, base):
    return all(not p.is_symlink() for p in (path.parent, *path.parent.parents) if p != base.parent and p.is_relative_to(base))


def _inspect_model(model, base):
    folder = relative_path(base, model.get('subdir') or model.get('repo_id') or model['id'])
    expected = dict({} if model.get('final_only_adaptation') else (model.get('expected_files') or {}))
    expected.update(model.get('generated_expected_files') or {})
    found, good, notes = 0, 0, []
    for name, spec in expected.items():
        p = relative_path(folder, name)
        if not no_link_parents(p, base):
            notes.append(name + '：目录链接未跟随'); continue
        try:
            info = p.lstat()
            found += 1
            if stat.S_ISLNK(info.st_mode):
                # 只对清单内 H3 共享权重检查最终文件元数据，不遍历外部目录、不读取权重。
                if model.get('reuse_family') != 'minimax-h3-mlx-serve' or p.suffix != '.safetensors':
                    notes.append(name + '：符号链接未验证'); continue
                target = p.resolve(strict=True)
                if target.name != p.name:
                    notes.append(name + '：共享文件名不匹配'); continue
                info = target.stat()
                notes.append(name + '：共享链接 → ' + str(target) + '（仅核对大小，未重算 SHA-256）')
            if stat.S_ISREG(info.st_mode) and any(isinstance(v.get('bytes'), int) and info.st_size == v['bytes'] for v in [spec, *spec.get('compatible_versions', [])]):
                good += 1
            else:
                notes.append(name + '：大小不符或非普通文件')
        except (OSError, RuntimeError):
            notes.append(name + '：缺失或不可读')
    # 同仓库多个组件共用目录，不能只凭目录存在误报组件安装。
    state = 'complete' if expected and good == len(expected) else ('partial' if found else 'missing')
    if not expected and folder.is_dir() and no_link_parents(folder / 'placeholder', base):
        state = 'partial'; notes.append('清单无完整文件尺寸记录，不能确认完整性')
    return {'model': model, 'path': folder, 'state': state, 'notes': notes, 'matched': good, 'total': len(expected)}


def inspect_model(model, base):
    """优先识别齐全的新目录，也兼容清单声明的旧目录；不搬迁或修改文件。"""
    options = [_inspect_model(model, base)]
    for relative in model.get('legacy_subdirs', []):
        item = _inspect_model(dict(model, subdir=relative), base)
        item['model'] = model
        options.append(item)
    rank = {'missing': 0, 'partial': 1, 'complete': 2}
    item = max(options, key=lambda x: (rank[x['state']], x['matched']))
    # 下载根保留上游相对路径；对外显示实际模型包目录，调用仍使用完整文件路径。
    if model.get('bundle_dir') and model.get('legacy_subdirs'):
        suffix = Path(model['bundle_dir']).relative_to(model['subdir'])
        item['display_path'] = item['path'] / suffix
    return item


def inventory(directory, catalog, templates):
    directory = safe_root(directory)
    # 只尝试选定根与其 models 子目录，不扫描整个主目录或任意树。
    bases = [directory]
    if (directory / 'models').is_dir() and not (directory / 'models').is_symlink():
        bases.insert(0, directory / 'models')
    models = []
    rank = {'missing': 0, 'partial': 1, 'complete': 2}
    for model in catalog['models']:
        if model.get('enabled') is False: continue
        options = [inspect_model(model, base) for base in bases]
        models.append(max(options, key=lambda x: (rank[x['state']], x['matched'])))
    by_id = {m['model']['id']: m for m in models}
    def dependencies_ok(item, seen=None):
        seen = set() if seen is None else seen
        ident = item['model']['id']
        if ident in seen: return False
        seen = seen | {ident}
        return item['state'] == 'complete' and all(d in by_id and dependencies_ok(by_id[d], seen) for d in item['model'].get('requires', []))
    install = directory.parent if directory.name == 'models' else directory
    runtimes = []
    default_scope = directory in (Path.home() / 'AI', Path.home() / 'AI/models')
    for runtime in templates.get('runtimes', []):
        candidates = [relative_path(install, p) for p in runtime.get('probes', [])]
        if default_scope or runtime['id'] == 'mlx-serve':
            candidates += [Path(p).expanduser() for p in runtime.get('standard_probes', [])]
        for p in candidates:
            # 可读入口/应用存在不等于运行成功；不会执行它们。
            try:
                if p.is_symlink() or not no_link_parents(p, install if p.is_relative_to(install) else p.parent): continue
                if p.is_file() and os.access(p, os.X_OK) or p.is_dir() and p.suffix == '.app':
                    runtimes.append(dict(runtime, detected_path=str(p))); break
            except OSError:
                continue
    for item in models:
        item['dependencies_ok'] = dependencies_ok(item)
        item['missing_dependencies'] = [d for d in item['model'].get('requires', []) if d not in by_id or not dependencies_ok(by_id[d])]
    return directory, install, models, runtimes


def render(directory, install, models, runtimes, templates):
    lines = [BEGIN, '## 本地安装检查与 API 调用说明', '', '检查目录：' + str(directory), '',
             '请将手写说明放在自动区标记之外；再次检查只更新标记区，不生成 API.md 备份；已有备份保持不动。', '', '仅检查清单中的文件、字节数及入口是否存在；未重算 SHA-256、未执行二进制、未验证推理，也不表示 API 服务正在运行。缺失项不会安装。', '', '### 工具入口', '']
    def expand(body, item=None, runtime=None):
        model = item['model'] if item else {}
        folder = item['path'] if item else directory
        first = next(iter(model.get('expected_files', {})), '')
        values = {'INSTALL_DIR': str(install), 'MODEL_DIR': str(item.get('display_path', folder) if item else folder), 'MODEL_FILE': str(folder / first),
                  'MODEL_ID': model.get('id', ''), 'BINARY_PATH': runtime.get('detected_path', '') if runtime else ''}
        for key, value in values.items():
            body = body.replace('{{' + key + '}}', shlex.quote(value))
            body = body.replace('{{' + key + '_JSON}}', json.dumps(value, ensure_ascii=False))
        return body
    for runtime in runtimes:
        lines += ['#### ' + runtime.get('name', runtime['id']), '', '已发现入口：' + runtime['detected_path'] + '（未运行验证）', '', expand(runtime.get('markdown', ''), runtime=runtime), '']
    if not runtimes: lines += ['未发现本次检查范围内的已知工具入口。', '']
    lines += ['### 模型与组件', '']
    labels = {'complete': '已知文件尺寸齐全（未验证推理）', 'partial': '不完整 / 待核查', 'missing': '未发现已知文件'}
    for item in models:
        m = item['model']
        if item['state'] == 'missing': continue
        lines += ['#### ' + m.get('name', m['id']), '', '- 标识：' + m['id'], '- 目录：' + str(item.get('display_path', item['path'])), '- 文件状态：' + labels[item['state']], '- 已匹配：%s/%s' % (item['matched'], item['total'])]
        if item['missing_dependencies']: lines += ['- 依赖尚未齐全：' + ', '.join(item['missing_dependencies'])]
        for note in item['notes']: lines.append('- ' + note)
        if m.get('license_notice'): lines.append('- 许可提醒：' + m['license_notice'])
        template = templates.get('models', {}).get(m['id'], {})
        runtime = next((r for r in runtimes if r['id'] in template.get('runtime_ids', []) or m.get('runtime') in [r['id']] + r.get('aliases', [])), None)
        lines += ['- 运行时入口：' + (runtime['detected_path'] if runtime else '本次检查范围内未发现；请先安装对应运行时'), '']
        template = templates.get('models', {}).get(m['id'], {})
        body = template.get('markdown', '')
        if body:
            lines += ['以下为调用参考；须先补齐文件、依赖并手动启动服务，不能据此认定当前可调用。', '', expand(body, item, runtime), '']
        elif m.get('source_url'): lines += ['模型来源：' + m['source_url'], '']
    missing = [m['model']['id'] for m in models if m['state'] == 'missing']
    lines += ['### 本次未发现', '', ', '.join(missing) if missing else '清单中的模型均有已知文件。', '', END]
    return '\n'.join(lines) + '\n'


def update_document(directory, block, agent_context=None):
    target = directory / 'API.md'
    if target.is_symlink(): raise ValueError('API.md 是符号链接，拒绝写入')
    if target.exists() and not stat.S_ISREG(target.lstat().st_mode):
        raise ValueError('API.md 不是普通文件，拒绝读取或写入')
    old = target.read_bytes().decode('utf-8') if target.exists() else ''
    if BEGIN in old or END in old:
        if old.count(BEGIN) != 1 or old.count(END) != 1 or old.index(BEGIN) > old.index(END):
            raise ValueError('API.md 自动区标记异常；为保护手写内容未修改')
        end = old.index(END) + len(END)
        if old[end:end + 1] == '\n': end += 1
        new = old[:old.index(BEGIN)] + block + old[end:]
    else:
        new = old + ('\n\n' if old and not old.endswith('\n\n') else '') + block
    if agent_context is not None:
        from agent_workflow import append_workflow
        new = append_workflow(new, **agent_context)
    if new == old: return '无变化'
    fd, temp = tempfile.mkstemp(prefix='.API.md.', dir=directory)
    try:
        # 完整写入并同步临时文件后才原子替换；失败保留原文档，不生成备份。
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(new)
            f.flush()
            os.fsync(f.fileno())
        if target.is_symlink(): raise ValueError('API.md 已变为符号链接，拒绝写入')
        os.replace(temp, target)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return '已更新' if old else '已生成'


def main(argv=None):
    parser = argparse.ArgumentParser(description='离线检查指定目录并补全 API.md，不安装、不启动服务')
    parser.add_argument('--directory', type=Path, default=Path.home() / 'AI', help='安装根目录或共享模型根目录（默认 ~/AI）')
    parser.add_argument('--if-present', action='store_true', help='自动钩子：没有已知安装文件时不创建文档')
    parser.add_argument('--catalog', type=Path, default=ROOT / 'catalog.json')
    parser.add_argument('--templates', type=Path, default=ROOT / 'config/api_templates.json')
    parser.add_argument('--services-root', type=Path, default=Path.home() / 'AI', help='原生服务配置根目录；用于生成 Agent 命令，不创建目录')
    args = parser.parse_args(argv)
    try:
        catalog = json.loads(args.catalog.read_text(encoding='utf-8'))
        templates = json.loads(args.templates.read_text(encoding='utf-8'))
        directory, install, models, runtimes = inventory(args.directory, catalog, templates)
        if args.if_present and not runtimes and not any(m['state'] != 'missing' for m in models):
            return 0
        model_root = directory / 'models' if (directory / 'models').is_dir() and not (directory / 'models').is_symlink() else directory
        status = update_document(directory, render(directory, install, models, runtimes, templates),
                                 agent_context=dict(service_root=args.services_root, models_dir=model_root))
        print(status + '：' + str(directory / 'API.md'))
        print('完整文件集 %s；待核查 %s；未发现 %s；工具入口 %s（均未做运行测试）' % tuple([sum(m['state'] == s for m in models) for s in ('complete', 'partial', 'missing')] + [len(runtimes)]))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('API 文档未更新：' + str(exc), file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
