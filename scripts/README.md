# macOS 交互式 ModelScope 下载器

本代码 MIT 开源。只负责下载与记录，不安装推理服务、不转换模型、不启动模型、不调付费 API。权重各自的许可证不受本项目 MIT 许可证替代。

## 普通使用：只开根目录菜单

在工具包根目录的终端运行 `bash start.sh`，选择查看模型、预览或下载。入口是 Bash，无需先装 Python；选中需要它的任务后，菜单会检查并引导准备 Python、uv 与官方下载客户端，准备成功后继续原任务。安装依赖仍保留必要确认，下载选好模型与目录后不再追加确认。

共享根默认 `~/AI/models`，可在本次菜单会话设置已挂载外置盘路径。下载器不安装推理服务、不自动加载模型。基础环境、AI 能力与模型下载都从同一个入口选择，不要求用户挨个运行脚本。

## 维护者参数参考

以下参数供审查、测试或自定义清单使用，不是普通用户必须输入的操作步骤。直接调用下载器需要 Python 3.10+；独立脚本自身不会隐式安装依赖，自动准备与续办由根入口负责。

- `scripts/download_models.py --list`：只读模型列表；加 `--details` 查看仓库、文件、精度和许可。
- `--dry-run`：只读预览，不联网、不创建目录。
- `--select MODEL_ID`：选择已核查的模型；多个 ID 逗号分隔，不默认全选。
- `--models-dir /绝对路径`：指定共享模型根。
- `--reuse-from /已有H3目录`：显式复用其他位置的既有 H3 文件；SHA256 核验一致后链接，不移动或删除源文件。「管理本地模型 → 复用已有 H3 权重」 提供同一操作。普通选择 H3 无需该参数，自动检查当前模型根目录内的两版清单路径及旧默认 `video/h3-mlx-8bit`，不递归扫描其他目录。
- `--repo OWNER/REPO --revision COMMIT_OR_TAG`：自定义 ModelScope 仓库，不猜测或回退 Hugging Face。
- `scripts/bootstrap_download_env.sh`：底层客户端安装步骤；普通使用由入口内部调用。

自定义仓库可使用重复的 `--include "文件模式"` / `--exclude "文件模式"`，例如 `--include "*.json" --include "*.safetensors"`。这只是语法示例，不是所有模型通用的完整依赖清单；有些仓库需要 `.model`、`.txt`、`.bin` 或独立 VAE。清单模式只能在 catalog 配置过滤，防止临时覆盖既有完整性要求。

默认共享根目录为 `~/AI/models`，独立于应用和工具包。清单项目按 `subdir` 分区，自定义仓库按 `<owner>/<repo>` 保存。`--models-dir` 指定根目录，旧参数 `--dest` 等价保留，两者不能同时使用；可放外置 SSD，空格路径必须引号包裹。路径仅对当前命令生效，不写自定义配置或应用设置。详见[共享模型目录](../docs/SHARED_MODELS.md)。现有文件不主动删除，续传/文件验证交给官方客户端。下载失败退出非零；重跑同一命令并检查官方客户端提示。不同模型串行下载，避免同时大量占用网络和磁盘。

`bootstrap_download_env.sh` 使用 `uv tool install modelscope-hub==0.4.0` 安装固定版本官方下载工具。工具有独立依赖环境，但 `ms-hub` 可从终端全局调用，不把依赖塞进项目，也不安装完整 `modelscope` 推理框架或 PyTorch。安装后的实际体积需自行核查；依赖未做跨平台哈希锁定。独立脚本不自动安装 uv；统一入口会先处理缺失依赖。不会悄悄替换现有工具。

下载器先查 PATH 中的 `ms-hub`；真实下载时还会查询 `uv tool dir --bin`，最后兼容旧版 `.venv-download/bin/ms-hub`。`--list` / `--dry-run` 不执行 uv 查询；找不到客户端时只预览 `ms-hub` 命令，不安装软件。

已有官方下载器时可指定 `--cli /绝对路径/ms-hub`，工具不会检查它是否为官方程序，请只指向可信安装。Python 脚本的 `--help`、`--list`、`--dry-run` 可在其他系统运行以便代码审查；真实下载和 bootstrap 限 macOS。

## 简洁选择与整套目录

默认菜单只有“大模型、音频、图像/视频”三组，每个可选项是一套模型，显示“仓库组织 / 模型名 格式 精度（用途）”及含必要组件的总大小；根目录只显示一次，选定后的下载计划显示完整最终目录。序号跨组连续，`--select 1,8` 或 `--select krea2-turbo` 均可；数字以当前 `--list` 为准，长期脚本应使用固定 id（通过 `--list --details` 查看）。不默认选择任何模型，未启用项目与内部组件不占菜单行。未知大小明确标为未知。

不传 `--models-dir` 时，交互下载只询问一次根目录，回车使用 `~/AI/models`。无人值守模式使用同一默认根目录。目录选择只对本次命令有效，不创建自定义配置。

- Qwen-Image-2.1-Turbo：排在 Krea2 前，三组件 BF16 约 32.44 GB，统一在 `images/Qwen-Image-2.1/`，保留 diffusion_models、text_encoders、vae 原路径。原生 RGBA 透明背景，用专属 2.1 VAE，不附加速 LoRA。
- Krea 2 只保留 Turbo：一次选择，主干、文本编码器、VAE 全部在 `images/Krea-2/` 内，主干与编码器保留原仓库路径；VAE 位于 `dependencies/Wan_2.1_ComfyUI_repackaged/` 下，保留其仓库路径。不再提供 Raw。

组件子目录内保留上游仓库路径，不擅自重命名文件。旧版分散目录不会自动移动或删除；H3 会自动检查当前模型根目录内的两版清单目录及旧默认目录，其他位置可显式选择已有目录校验复用；请核对路径和磁盘余量。原始转换素材（Music3、H3、YuE2）不再提供为可选项。Music3 与 H3 仅提供已核查的 ddalcu/mlx-serve 8bit 转换包（不是满精度），Music3 约 13.55 GB；H3 FL2VA 约 70.08 GB，REF2VA 约 69.30 GB。YuE2 提供已核查的 `audio-cpp/Yue2-3B-GGUF` 高精度包，约 7.79 GB：BF16 主模型、F32 VAE、四个外置附件及 README，不含其他量化、示例和 LoRA；运行时需显式选择 BF16/F32 文件，不沿用 Q8/F16 默认值。权重仅限非商业用途。

`--dry-run` 默认只显示简洁计划，不再输出大量逐文件命令；加 `--details` 才显示组件、精度、说明和命令 JSON。选好模型和目录后，只显示“可用磁盘 … GB；预计下载 … GB”便直接开始下载，不再重复模型清单或询问 yes、许可确认。各组件许可保留在 `--details` 和清单中；工具不会替你接受网站受限条款、登录或绕过访问限制。旧版 `--yes` / `--accept-licenses` 仅兼容解析，不再有作用。下载成功不等于已验证每个模型在 Mac 上运行。

## 清单与许可

默认读取项目根 `catalog.json`；也可 `--catalog /path/custom.json`。示例结构见 `catalog.example.json`，其中占位项不可下载。字段：

- `schema_version: 1`，`models` 数组。
- `id`：选择用唯一名称；`repo_id`：已核实 ModelScope `owner/name`。
- `requires`：可选依赖 id 数组；选择整套模型后自动递归加入必要组件，并按依赖优先顺序下载、同一 id 仅一次。依赖缺失、未启用、循环或目录冲突都会中止。默认列表和预览只显示整套模型，`--details` 展开组件；自定义 `--repo` 不猜测依赖。
- `group`：`llm`、`audio`、`image_video` 分别对应“大模型、音频、图像/视频”。`component:true` 的内部组件不单独出现在菜单中。
- `short_name`：简洁显示名；`bundle_dir`：整套模型所在目录，具体组件仍各自使用安全的 `subdir`。
- `revision`：默认 `master`，更可复现的方案是自己核查并填写 commit。分支/标签可能变化。
- `subdir`：可选安全相对目录，否则按仓库 owner/name；禁止 `..`、绝对路径及经过符号链接的目标子目录。H3 受控复用可在模型目录内建立已核验文件的链接，并记录来源；不等于放宽目标根的路径检查。
- `precision`、`readiness`、`notes`：说明原始精度、格式兼容/转换要求。下载原始 BF16/FP16 不等于 MLX 或 CPP 可直接加载。
- `enabled:false` 或 `repo_id:null`：禁止选择，明确保留尚未验证的条目。
- `license`、`license_url`、`license_notice`：许可、来源、地区及用途限制。保留许可信息供按需查看，不自动判断所在地，也不会代替用户接受网站的受限条款或处理登录。
- `include` / `exclude`：官方 glob 过滤；对混合精度整仓必须核实所需组件再填，不能只保留权重而漏 tokenizer/config/VAE。
- `disk_gb`：可选粗估下载体积，按十进制 GB 预警，不保证足够；模型解压、临时文件、后续转换、缓存另需空间。

公有模型优先匿名下载。本脚本不收集、不保存、不打印 token，也不提供登录功能；如果官方客户端已独立配置登录，客户端可能沿用已有凭据。不要把 token 放进 catalog、命令参数或错误报告。受限仓库请由你单独按官方方法处理，不自动接受许可或创建账号。

## 校验与记录

每批下载写入目标根目录 `Mac-AI-Manifests/<UTC时间>-<随机ID>.json`：清单 SHA256、仓库、请求 revision、过滤条件、完成/未完成状态、文件路径和字节数。普通有 `expected_files` 的条目默认核对文件存在与大小；加 `--sha256` 时还核对已记录的SHA256，仓库更新造成差异会报错并要求重新审核。`--sha256` 也会计算其余已下载文件哈希，大模型会增加完整磁盘读取耗时。

H3 复用路径例外：即使未加 `--sha256`，也按固定快照完整核验 SHA256 后才使用现有文件、链接文件或新下载文件；记录链接及解析后的源路径。只读预览不读取大权重做哈希，实际下载前才校验并给出复用量。

`resolved_commit:null` 是刻意保留：当前包装器不伪称已取得不可变 commit；默认 master 不完全可复现。记录 `downloaded` 仅指官方客户端成功退出，不代表模型兼容性、质量或推理通过。CLI 仍可能下载仓库中的代码文件，但本工具不会导入或执行它们。不要开启未知仓库的 `trust_remote_code`。

## 离线测试与限制

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/bootstrap_download_env.sh
```

本交付已执行离线 mock 测试：只读模式、macOS 限制、恶意仓库参数、路径越界和符号链接、参数列表调用、成功/失败清单、SHA256、无额外确认下载。尚未在用户 Mac 上安装、联网下载或推理实测。Mock 不证明模型源可用；正式下载前请检查仓库和许可证。

## 官方依据（2026-10-20 核查）

- [官方下载客户端](https://github.com/modelscope/modelscope_hub)：README 的下载 CLI 支持 `--local-dir`、revision、过滤和并发；SDK 提供续传与校验。README 列出 0.4.0 发布于 2026-09-01。
- [官方 pyproject](https://github.com/modelscope/modelscope_hub/blob/main/pyproject.toml)：Python >=3.10；基础依赖 requests、tqdm、filelock、urllib3、cryptography，而非推理框架。
- [PyPI](https://pypi.org/project/modelscope-hub/0.4.0/)：本工具固定客户端版本；原项目许可证 Apache-2.0。


### 命令从哪个目录运行？

本文带 `scripts/` 前缀的命令从工具包根目录运行。如果终端已经进入 `scripts` 子目录，请去掉这层前缀，例如 `python3 download_models.py --list`；或者先执行 `cd ..`，再运行 `python3 scripts/download_models.py --list`。不要在 `scripts` 目录内再次加 `scripts/`，否则 Python 会查找不存在的 `scripts/scripts/download_models.py`。目录名包含空格时，切换目录请用引号包住完整路径，例如 `cd "/你的路径/Mac-AI-Kit"`。

工具包自编帮助、提示和注释采用中文；命令参数、模型名、协议字段和路径保持原样。MIT 许可证正文保留法律原文；第三方安装器或系统工具自己的输出可能仍为英文。

音乐权重统一保存到共享根的 `music/`：YuE2 为 `music/Yue2-3B-GGUF/`，Music3 为 `music/MiniMax-Music3-MLX-Serve-8bit/`；ASR、Whisper 等语音权重放 `audio/`。菜单仍归“音频”组。旧目录不自动迁移或删除；H3 会自动检查当前根目录内的两版清单目录及旧默认目录，其他位置可显式选择已有目录校验复用。

H3 FL2VA 与 REF2VA 可分别选择或一并下载；双包共有文件按 SHA256 校验后复用，缺失/不同文件仍下载。已有一版时普通选择另一版即可自动校验复用，不必进入手动复用入口；两版同选优先已有版本。仅其他位置需要「管理本地模型 → 复用已有 H3 权重」 或 `--reuse-from` 显式指定。自动来源中的越界链接、断链、循环链接或 SHA256 不符文件不会复用；目标已存在但损坏则停止，不覆盖。不同 transformer 仍分别下载。源文件须一直保留，移动或拔盘会使链接失效。REF2VA 支持音频参考，FL2VA 不支持，详见 [H3 说明](../docs/H3.md)。
