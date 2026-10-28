#!/usr/bin/env python3
"""调用 SD.cpp 原生模型管理接口；不启动进程、不下载模型、不转发推理。"""
import argparse
import json
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

SELECTIONS = {
    'qwen-image-21-turbo': {
        'diffusion_model': 'diffusion_models/qwen_image_2.1_turbo_bf16.safetensors',
        'llm': 'text_encoders/qwen3vl_8b_bf16.safetensors',
        'vae': 'vae/qwen_image_2.1_vae_bf16.safetensors',
    },
    'krea2-turbo': {
        'diffusion_model': 'diffusion_models/krea2_turbo_bf16.safetensors',
        'llm': 'text_encoders/qwen3vl_4b_bf16.safetensors',
        'vae': 'vae/wan_2.1_vae.safetensors',
    },
}


def request(base, endpoint, payload=None, timeout=15):
    """保留服务端错误；409 不重试、不取消任务，也不关闭进程。"""
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(base.rstrip('/') + '/sdcpp/v1/' + endpoint, data=data,
                  headers={'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read(65536).decode('utf-8', errors='replace')
        raise ValueError(f'SD.cpp 返回 HTTP {error.code}：{detail}') from error


def selection(recipe, files):
    """必须使用服务器返回的 opaque id；重名组件拒绝猜测，改用 WebUI 选择。"""
    result = {}
    for slot, relative in SELECTIONS[recipe].items():
        found = [f for f in files if f.get('relative_path') == relative
                 and f.get('category') == relative.split('/')[0] and isinstance(f.get('id'), str) and f['id']]
        if len(found) != 1:
            raise ValueError(f'{relative}：找到 {len(found)} 个候选，请在 WebUI 选择组件；未切换模型')
        result[slot] = found[0]['id']
    return {'recipe': recipe, 'models': result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:12345', help='已启动的 SD.cpp 地址')
    parser.add_argument('action', choices=['status', 'load', 'unload'], help='查看状态、加载或卸载')
    parser.add_argument('recipe', nargs='?', choices=list(SELECTIONS), help='加载的图像配方')
    args = parser.parse_args(argv)
    url = urlsplit(args.url)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        parser.error('地址必须是 http(s)://主机:端口，不含凭据或路径')
    if args.action == 'load' and not args.recipe:
        parser.error('load 必须指定配方')
    try:
        if args.action == 'load':
            recipes = request(args.url, 'recipes').get('recipes', [])
            if args.recipe not in [r.get('id') for r in recipes]:
                raise ValueError('服务器未提供此配方；请更新 SD.cpp')
            files = request(args.url, 'model-files').get('files', [])
            payload = selection(args.recipe, files)
            print('正在加载 ' + args.recipe + '…', flush=True)
            result = request(args.url, 'models/load', payload, timeout=1800)
        elif args.action == 'unload':
            result = request(args.url, 'models/unload', {}, timeout=120)
        else:
            result = request(args.url, 'models/status')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print('未完成：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
