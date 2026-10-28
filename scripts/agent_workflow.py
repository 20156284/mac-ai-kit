#!/usr/bin/env python3
"""生成可追加的 Agent 资源约定；不操作服务、模型或网络。"""
import hashlib
import json
from pathlib import Path
import re
import shlex


ROOT = Path(__file__).resolve().parents[1]


def append_workflow(old, service_root, models_dir, toolkit_root=None):
    """相同标记和实际路径只追加一次；移动工具包后追加新路径，不改旧文。"""
    paths = [Path(path).expanduser().absolute() for path in (toolkit_root or ROOT, service_root, models_dir)]
    if any(any(char in str(path) for char in '\r\n') or '..' in path.parts for path in paths):
        raise ValueError('Agent 调用路径无效')
    toolkit, root, models = paths
    digest = hashlib.sha256(json.dumps([str(path) for path in paths], ensure_ascii=False).encode()).hexdigest()[:16]
    marker = '<!-- MAC-AI-KIT:AGENT-WORKFLOW:' + digest + ' -->'
    previous = re.findall(r'<!-- MAC-AI-KIT:AGENT-WORKFLOW:(?:v\d+:)?[0-9a-f]+ -->', old)
    if previous and previous[-1] == marker:
        return old
    command = 'python3 ' + shlex.quote(str(toolkit / 'scripts/native_services.py'))
    command += ' --root ' + shlex.quote(str(root)) + ' --models-dir ' + shlex.quote(str(models))
    lines = [marker, '## Agent 资源协调与主动启停', '',
             '这是本机用户指定的资源使用策略，不是各框架天生不能并行。Agent 应按本节协调；工具包不会自动执行或定时调度。若文档包含多个路径版本，以最后追加的本节为准；旧版模型模板不构成绕过本策略的授权。', '',
             '### 保留 LLM，视频与音频／制图互斥', '',
             '- 大语言模型是必须保留的基础能力：不关闭或卸载 LLM，不退出整个 MLX-Serve App，也不以释放内存为由替换 LLM。',
             '- 启动视频前，先确认要继续使用的 LLM 已加载并能响应；不要先开视频再排队冷加载 LLM。部分已核对版本的视频步骤让行仅服务已驻留 LLM，不保证所有安装版本都能交错执行。LLM 与视频合计装不下时停止切换，请用户选择更小的视频配置或明确调整方案，不能悄悄挤掉 LLM。',
             '- 视频任务（如 MLX App 中的 H3）启动前，应结束并释放 SD.cpp 制图和 audio.cpp 音频服务；视频运行期间不启动这两项。',
             '- 从视频转回音频或制图时，先确认视频任务真正完成、没有排队，再只卸载视频模型；保留 LLM 与 App。然后只启动本次所需的音频或制图，不默认启动全部。',
             '- 本工具不托管视频服务，也不能替 Agent 证明 MLX 视频任务空闲。视频任务状态或单模型卸载接口不明确时，暂停切换并向用户确认；不要猜测端点、杀进程或整体退出 App。',
             '- 资源互斥不是任务取消授权：正在生成、转写、加载或排队时必须等待。请求超时、客户端退出、API 健康、模型已驻留、CPU 暂时低都不能证明空闲。', '',
             '### 当前工具包的无菜单命令', '',
             '以下绝对路径取自生成本节时的实际工具包位置与本次配置；更新或移动目录后先从新工具包刷新本节。不会从旧路径猜测新的安装位置。', '',
             '```bash',
             '# 只读 JSON：进程 / 归属 / API / 工作负载是不同状态，unknown 不是 idle',
             command + ' status --json',
             command + ' status --service audio --json',
             command + ' status --service sd-qwen --json', '',
             '# 视频已完成并仅卸载视频模型后，按本次任务选择一项',
             command + ' start --service audio',
             command + ' start --service sd-qwen',
             '```', '',
             'JSON 的 workload / stop_safety 为 unknown 时不能推断可自动停止；model_residency=empty/ready 也不是 idle。退出码非零、supported=false 或任一 error 都不能当作已停止。启动仍执行模型、端口与内存预检；拒绝时不关闭 LLM、不绕过预算、不自动停另一服务。服务监听所有网卡但无认证，仅在可信局域网 / VPN 中使用。', '',
             '### 停止前必须证明任务已结束', '',
             '下面 stop 命令本身可能中断正在进行的任务，不能仅凭健康检查或瞬时 idle 就无人值守执行。Agent 如能独立证明服务归属、独占全部提交入口、所有提交方停止新请求、所有同步与异步任务及队列已完成，可按本策略主动停止，无需逐次重复询问。JSON 中的 unknown 不是空闲证明，但不否定另有可靠的独占协调与任务账本证据。只有无法建立这些保证时，才等待或向用户确认当前可停止；不能把此前的切换策略当成取消任务授权。', '',
             '```bash',
             '# 仅在上述条件成立，或用户对当前停止明确确认后执行；不是无条件自动步骤',
             command + ' stop --service sd-qwen',
             command + ' stop --service audio',
             '# 必须再次确认无 loaded / pid / stopping；未确认退出就不启动视频',
             command + ' status --json',
             '```', '',
             '停止与 JSON 快照只覆盖本工具根归属的原生服务，不代表整台机器上的其他手动实例均已停止；外部进程、其他端口或归属不符必须另外核实，无法核实就暂停切换。这里不强杀，不操作 LLM、视频或外部进程。超时或停止中应继续观察，不重复提交媒体任务。API.md 是历史调用说明，实际状态以本次只读查询为准；status / stop / logs 不改本文档。', '',
             '核查依据：当前 audio.cpp 默认配置没有权威的 busy / queue 查询；未启用实验并行模式。SD.cpp 的模型卸载可原子拒绝 busy，但不是全局停机屏障，其他客户端能重新加载或提交独立放大任务；不能把卸载成功当作可立即停机。', '',
             '- [audio.cpp 固定版运行时](https://github.com/0xShug0/audio.cpp/blob/v0.9.2-drama3-base-small/app/server/runtime.cpp#L2235-L2264)',
             '- [SD.cpp 模型切换与 busy 保护](https://github.com/leejet/stable-diffusion.cpp/blob/master-1006-0886f11/examples/server/routes_models.cpp#L56-L71)',
             '- [SD.cpp 独立放大接口](https://github.com/leejet/stable-diffusion.cpp/blob/master-1006-0886f11/examples/server/api.md#L989-L1033)', '']
    return old + ('\n' if old.endswith('\n') else '\n\n' if old else '') + '\n'.join(lines)
