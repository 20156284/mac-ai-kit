"""只读发现服务模型与同源 tokenizer；不猜测相近模型，不加载模型代码。"""

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
REPO = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z')
LIMIT = 2_000_000
LOCAL_GROUPS = {'llm', 'audio', 'music', 'images', 'video', 'models'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('模型发现拒绝重定向，请填写最终接口地址')


def discover(url):
    # 不继承代理或凭据，不跟随重定向，不探测其他服务。
    request = Request(url.rstrip('/') + '/models', headers={'Accept': 'application/json'})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=8) as response:
        raw = response.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('模型列表过大')
    data = json.loads(raw)
    rows = data.get('data') if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError('接口没有返回有效 data 模型列表；请检查 base URL（通常以 /v1 结尾）')
    result, seen = [], set()
    for row in rows:
        model = row.get('id') if isinstance(row, dict) else None
        if (
            not isinstance(model, str)
            or not model
            or model.startswith('-')
            or any(ord(c) < 32 or ord(c) == 127 for c in model)
        ):
            raise ValueError('模型列表包含无效 id')
        if model in seen:
            raise ValueError('模型列表包含重复 id，无法安全选择')
        seen.add(model)
        result.append(row)
    return result


def choose(rows, title, label):
    if len(rows) == 1:
        return rows[0]
    print(title)
    for index, row in enumerate(rows, 1):
        print(f'  {index}. {label(row)}')
    answer = input('编号（q 取消）：').strip()
    if not answer or answer.lower() == 'q':
        raise EOFError
    if not answer.isdecimal() or not 1 <= int(answer) <= len(rows):
        raise ValueError('选择无效，已返回；不会默认选择第一个模型')
    return rows[int(answer) - 1]


def safe_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > LIMIT:
        return {}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def public_repo(value):
    # 两段本地分类路径不是公共仓库；也不接受点路径或父目录。
    return (
        isinstance(value, str)
        and bool(REPO.fullmatch(value))
        and value.split('/')[0] not in LOCAL_GROUPS
        and all(part not in ('.', '..') for part in value.split('/'))
    )


def default_model_root():
    """独立 Python 入口也读取菜单保存的目录，不执行配置内容。"""
    config = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'mac-ai-kit/model-directory'
    if not config.exists() and not config.is_symlink():
        return Path.home() / 'AI/models'
    if config.is_symlink() or not config.is_file() or config.stat().st_size > 16384:
        raise ValueError('模型目录设置不是有效普通文件；请在菜单重新设置，不会退回默认目录')
    value = config.read_text(encoding='utf-8').strip()
    if not value or '\n' in value or '\r' in value or not Path(value).expanduser().is_absolute():
        raise ValueError('已保存的模型目录无效；请在菜单重新设置，不会退回默认目录')
    return Path(value).expanduser()


def folder_names(folder, base):
    relative = folder.relative_to(base).as_posix()
    return {folder.name, str(folder), relative, './' + relative}


def local_candidates(base):
    base = base.expanduser().absolute()
    if any(p.is_symlink() for p in (base, *base.parents)):
        raise ValueError('模型目录含符号链接；请在模型菜单选择真实目录')
    catalog = safe_json(ROOT / 'catalog.json').get('models', [])
    mappings = safe_json(ROOT / 'config/benchmark_tokenizers.json').get('models', [])
    known = {}
    for model in catalog:
        if model.get('group') == 'llm' and not model.get('component'):
            folder = base / model.get('subdir', model['repo_id'])
            if any(p.is_symlink() for p in (folder, *folder.parents)):
                continue
            names = {model['id'], model['repo_id']} | folder_names(folder, base)
            evidence = ['catalog.json:' + model['id']]
            for mapping in mappings:
                if mapping['repo_id'] == model['repo_id']:
                    tokenizer = folder / 'tokenizer.json'
                    # 量化名称映射不仅凭 base_model：已有文件须与审计过的原版完全一致。
                    identical = (
                        tokenizer.is_file()
                        and not tokenizer.is_symlink()
                        and tokenizer.stat().st_size <= 100_000_000
                        and hashlib.sha256(tokenizer.read_bytes()).hexdigest() == mapping['tokenizer_sha256']
                    )
                    if not tokenizer.exists() or identical:
                        names.add(mapping['base_model'])
                        evidence.extend([mapping['source'], 'tokenizer SHA256: ' + mapping['tokenizer_sha256']])
            known[folder] = (names, evidence, {model['repo_id']})
    # 有界扫描用户配置根目录，不扫描整台机器、不读权重、不跟随符号链接。
    visited = 0
    if base.is_dir():
        for current, dirs, files in os.walk(base, followlinks=False):
            folder = Path(current)
            visited += 1
            if visited > 5000:
                raise ValueError('模型目录过大；请在模型菜单缩小根目录后重试')
            dirs[:] = [d for d in dirs if not d.startswith('.') and not (folder / d).is_symlink()]
            if len(folder.relative_to(base).parts) >= 4:
                dirs[:] = []
            if 'tokenizer.json' not in files and 'config.json' not in files:
                continue
            config = safe_json(folder / 'config.json')
            if config.get('model_type') in ('clip', 't5', 'whisper'):
                continue
            names, evidence, repos = known.get(folder, (folder_names(folder, base), ['本地目录'], set()))
            for filename in ('config.json', 'tokenizer_config.json'):
                for key in ('_name_or_path', 'name_or_path'):
                    value = safe_json(folder / filename).get(key)
                    if public_repo(value):
                        names.add(value)
                        if folder not in known:
                            repos.add(value)
                        evidence.append(f'{folder / filename}:{key}={value}')
            known[folder] = (names, evidence, repos)
    result = []
    for folder, (names, evidence, repos) in known.items():
        if not folder.is_dir() or any(p.is_symlink() for p in (folder, *folder.parents)):
            continue
        tokenizer = folder / 'tokenizer.json'
        has_tokenizer = tokenizer.is_file() and not tokenizer.is_symlink()
        if has_tokenizer or repos:
            result.append(
                {
                    'path': str(folder),
                    'names': names,
                    'evidence': evidence,
                    'has_tokenizer': has_tokenizer,
                    'repos': repos,
                }
            )
    return result


def candidate_resolution(chosen, confirmed=False):
    evidence = chosen['evidence']
    if confirmed:
        evidence = ['用户明确选择服务别名对应目录', *evidence]
    if chosen['has_tokenizer']:
        return {'kind': 'local', 'value': chosen['path'], 'evidence': evidence}
    if len(chosen['repos']) == 1:
        return {'kind': 'public', 'value': next(iter(chosen['repos'])), 'evidence': evidence}
    raise ValueError('该目录没有 tokenizer.json，且原仓库元数据有歧义，已返回')


def resolve_tokenizer(model, base):
    candidates = local_candidates(base)
    identities = {model['id']}
    root = model.get('root')
    if isinstance(root, str) and root:
        identities.add(root)
    matched = [x for x in candidates if identities & x['names']]
    if matched:
        chosen = choose(matched, '同名模型目录不唯一，请选择正在服务的版本：', lambda x: x['path'])
        return candidate_resolution(chosen)
    # 清单可证明相对路径对应的真实来源；即使目录未下载，也绝不拿 llm/... 联网。
    known_repos = set()
    for item in safe_json(ROOT / 'catalog.json').get('models', []):
        if item.get('group') != 'llm' or item.get('component'):
            continue
        folder = Path(base).expanduser().absolute() / item.get('subdir', item['repo_id'])
        names = folder_names(folder, Path(base).expanduser().absolute()) | {item['id'], item['repo_id']}
        if identities & names and public_repo(item['repo_id']):
            known_repos.add(item['repo_id'])
    if len(known_repos) == 1:
        repo = known_repos.pop()
        return {'kind': 'public', 'value': repo, 'evidence': ['catalog.json 精确目录与仓库映射: ' + repo]}
    if len(known_repos) > 1:
        raise ValueError('清单中模型来源不唯一，无法确定同源 tokenizer')
    local_ids = [x for x in identities if x.startswith(('/', './', '../', '~/')) or x.split('/')[0] in LOCAL_GROUPS]
    if local_ids:
        raise ValueError(
            f'未在模型目录 {base} 找到 {local_ids[0]} 的 tokenizer.json；请检查模型目录设置或补齐同模型文件，不会把本地路径当作 Hugging Face 仓库'
        )
    # 只有完整原仓库 ID 或服务明确提供的 root 可以作为公共来源；不去掉量化后缀猜原模型。
    repos = {x for x in identities if public_repo(x)}
    if len(repos) == 1:
        repo = repos.pop()
        return {
            'kind': 'public',
            'value': repo,
            'evidence': ['由服务 /models 的 repo 形式标识推定，未独立验证模型身份: ' + repo],
        }
    if len(repos) > 1:
        raise ValueError('服务 id 与 root 指向不同公共仓库且无匹配本地 tokenizer，无法确定同源 tokenizer')
    if candidates:
        print('服务使用别名，无法证明它对应哪个本地模型。只选择你确认正在服务的同一模型；不确定请 q 取消。')
        # 别名即使只有一个候选，也必须主动确认，不能把唯一安装模型当正在服务模型。
        for index, row in enumerate(candidates, 1):
            print(f'  {index}. {row["path"]}')
        answer = input('对应本地模型编号（q 取消）：').strip()
        if not answer or answer.lower() == 'q':
            raise EOFError
        if not answer.isdecimal() or not 1 <= int(answer) <= len(candidates):
            raise ValueError('选择无效，已返回')
        chosen = candidates[int(answer) - 1]
        return candidate_resolution(chosen, confirmed=True)
    raise ValueError(
        '未找到同源本地 tokenizer，服务也未提供完整原仓库 ID。请让服务 /models 返回原仓库 root，或把同模型 tokenizer.json 放入所选模型目录后重试；不使用通用 GPT2 近似。'
    )
