"""模型写入根目录的纯路径检查；不创建、迁移或读取模型文件。"""

import os
from pathlib import Path


def validate_model_root(value):
    """清理输入整体首尾空白，再拒绝内部组件边缘空白；不修改旧来源。"""
    raw = os.fspath(value).strip()
    expanded = os.path.expanduser(raw)
    components = expanded.split('/')
    if any(part != part.strip() for part in components):
        suggestion = '/'.join(part.strip() for part in components)
        raise ValueError(
            f'模型目录组件首尾含空白：{raw!r}。'
            f'建议相邻规范路径：{suggestion!r}；请显式重新选择。'
            '不会自动改名、迁移或删除旧目录。'
        )
    if not raw or any(char in expanded for char in ('\n', '\r', '\x00')):
        raise ValueError(f'模型目录无效：{raw!r}')
    if '..' in components:
        raise ValueError(f'模型目录不能包含 ..：{raw!r}')
    path = Path(os.path.abspath(expanded))
    if path == Path(path.anchor):
        raise ValueError(f'不能使用文件系统根目录：{raw!r}')
    return path
