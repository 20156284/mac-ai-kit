#!/usr/bin/env python3
"""将 Remote 源文件嵌入独立启动脚本，不运行服务或安装依赖。"""

import argparse
import base64
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def render(root=ROOT):
    source = root / 'remote' / 'src'
    template = (source / 'launcher.sh').read_text(encoding='utf-8')
    for marker, name in (
        ('RUNTIME', 'runtime.cjs'),
        ('GUARD', 'bind-guard.cjs'),
        ('ADAPTER', 'studio-adapter.cjs'),
    ):
        token = f'{marker}_PAYLOAD_PLACEHOLDER'
        if template.count(token) != 1:
            raise ValueError(f'模板占位符必须恰好出现一次：{token}')
        template = template.replace(token, base64.b64encode((source / name).read_bytes()).decode('ascii'))
    return template.encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='只检查发布脚本与源码是否一致，不写文件')
    args = parser.parse_args()
    target = ROOT / 'remote' / 'start-remotion-remote.sh'
    expected = render()
    if args.check:
        if not target.is_file() or target.read_bytes() != expected:
            print('Remote 发布脚本与源码不一致，请运行 python3 scripts/build_remote.py。', file=sys.stderr)
            return 1
        print('Remote 发布脚本与源码一致。')
        return 0
    if target.is_symlink():
        parser.error('发布目标为符号链接，未写入。')
    target.write_bytes(expected)
    target.chmod(0o755)
    print('已生成 remote/start-remotion-remote.sh。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
