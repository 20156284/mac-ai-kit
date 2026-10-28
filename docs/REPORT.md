# 方案报告：精简推理，不重复堆叠框架

## 1. 选择结论

以 **MLX-Serve 为原生综合推理入口、llama.cpp 为独立 GGUF 语言模型后端、stable-diffusion.cpp 为制图与 H3 的独立 CPP 路径、audio.cpp 为音频独立 CPP 路径、llama-swap 为跨服务按需启停与路由层**。

四项推理组件按需选择，llama-swap 是完全可选的 GGUF 路由工具。独立 llama.cpp 不再只是备选：它有自己的版本、启动参数、API 和卸载生命周期。MLX-Serve 内嵌的 llama.cpp 路径不能替代这项独立后端。

但同一任务只选择一个执行后端：MLX 权重优先交给 MLX-Serve；已核验兼容的 GGUF LLM 交给独立 llama-server。不要因为五项都在方案中就同时加载重复权重。llama.cpp 不接管 sd.cpp/audio.cpp 的生成管线，也不是 llama-swap 的替代名称。

第六组件为 **Hermes Agent CLI**，安装顺序放在上述五项之后。它是 Agent/工具执行与工作流客户端，不是新的模型推理后端；可连接已经验收的本地兼容 API，也可经用户选择连接远程服务。本包不自动选择 provider、保存凭证或启动常驻 gateway。

### 六组件能力、界面和安装体积

| 组件 | 本方案用途 | 官方原生桌面.app/DMG | 其他入口 | 已核实发行包大小，不是安装占用 |
|---|---|---|---|---|
| ddalcu/mlx-serve | 原生LLM及综合媒体引擎；支持范围以安装版为准 | 有 | 桌面 App、OpenAI/Anthropic等API | 26.10.2 DMG约94.25MB |
| ggml-org/llama.cpp | 独立 GGUF LLM/VLM 推理；本方案先单模型文本服务 | 本包采用 CLI，不附加桌面应用 | llama-server、WebUI、OpenAI兼容API | v0.7.0链接的b11669 macOS arm64约11.6MB |
| llama-swap | 统一入口、按需启动与卸载、静态互斥/共存 | 未发现 | CLI、内置WebUI | v265 darwin arm64约21MB |
| stable-diffusion.cpp | Qwen-Image-2.1-Turbo、Krea2制图；H3视频 | 未发现 | CLI、sd-server、嵌入WebUI | 官方 latest macOS 包；体积以该次发行资产为准 |
| 0xShug0/audio.cpp | 音乐与 ASR CPP 后端 | 未发现 | CLI、audiocpp_server、WebUI/Studio/Arena | v0.9.2-drama3-base-small macOS arm64 Metal约31.2MB |
| NousResearch/hermes-agent | CLI Agent；调用本地/远程模型 API、工具与工作流 | 本方案不安装 desktop | hermes CLI；上游也有 TUI/web/gateway | v0.22.2 源码安装及其依赖，未实测总占用 |

前五件套所选下载包合计约193MB，仅为上述选定发行资产的粗加，不含模型、系统运行库、安装展开、构建工具或可选前端。**各项实际安装后占用尚未统一实测。** 不拿一个CLI文件与另一个完整.app比较。该193MB包含 MLX-Serve 的 DMG 口径；本版只安装桌面 App，由用户手动开启 API；也不包含 Hermes 的源码、Python/uv/Node/依赖与构建产物。

llama.cpp v0.7.0发行2026-10-15，其发行页链接b11669二进制；llama-swap发行2026-10-14；sd.cpp所选构建2026-10-18；audio.cpp所选构建2026-10-19。独立桌面前端可能是第三方，必须额外计算其包体与依赖，不默认加入。

[llama.cpp发行](https://github.com/ggml-org/llama.cpp/releases/tag/v0.7.0) · [llama.cpp资产](https://github.com/ggml-org/llama.cpp/releases/expanded_assets/b11669) · [llama-swap资产](https://github.com/mostlygeek/llama-swap/releases/expanded_assets/v265) · [sd.cpp资产](https://github.com/leejet/stable-diffusion.cpp/releases/expanded_assets/master-1006-0886f11) · [audio.cpp发行](https://github.com/0xShug0/audio.cpp/releases)

### 为什么不选 oMLX

这是本项目的取舍，而不是断言 oMLX 技术较差：

- 用户明确偏好更小的发行包、更少 Python 运行环境和更轻的运维体系。
- oMLX 在 MLX、mlx-lm、mlx-vlm、mlx-embeddings、mlx-audio 及 Python/Web 服务之上集成，能力丰富，但与本方案的服务职责重叠。
- MLX-Serve 采用 Zig 服务和 Swift 桌面界面，依赖 MLX / mlx-c、内嵌 llama.cpp 等原生库；**无 Python 不等于无框架依赖**。
- oMLX 的连续批处理、RAM/SSD KV cache、模型管理有价值；MLX-Serve 当前也有连续批处理、前缀缓存、内部 LRU 和空闲卸载，不能把这些称为前者独有。
- **不根据语言判断性能**：Python 服务的大量矩阵运算仍在原生代码/Metal 上；没有相同模型、量化、上下文和硬件的对照实测，不能声称原生版一定更快。
- oMLX 有打包好的 DMG，用户未必需要手工维护 Python；不采用它的原因仍是本项目对体积和职责精简的偏好。

### 体积证据（不含模型与缓存）

| 软件/版本 | 官方下载包 | 安装后 .app | 说明 |
|---|---:|---|---|
| MLX-Serve 26.10.2，2026-10-12 | 94,249,362 B，约94.25MB | 未核实 | 完整 DMG，不是单个约7MB服务端程序 |
| oMLX 0.7.1，2026-10-20，macOS26/27 | 873,741,990 B，约873.74MB | 未核实 | 下载包约为前者9.27倍，不能推算安装后倍数 |
| oMLX 0.7.1，macOS15 | 836,309,064 B，约836.31MB | 未核实 | 不与另一平台包混用比较 |

已读取官方发行信息并静态检查下载包；未在 Mac 上安装测量应用占用。oMLX 的 Python、科学计算、音频和文档依赖会增加包体，但不能错误归因于“附带完整 PyTorch”：当前打包代码包含 torch stub，并不等同捆绑真实 PyTorch。

来源：[oMLX发行](https://github.com/jundot/omlx/releases/tag/v0.7.1)、[MLX-Serve发行](https://github.com/ddalcu/mlx-serve/releases/tag/v26.10.2)、[oMLX依赖](https://github.com/jundot/omlx/blob/v0.7.1/pyproject.toml)、[oMLX打包](https://github.com/jundot/omlx/blob/v0.7.1/packaging/build.py)、[MLX-Serve原生依赖](https://github.com/ddalcu/mlx-serve/blob/main/.gitmodules)。

## 2. 不采用或不默认启用的方案

| 方案 | 本项目取舍 | 不应误解为 |
|---|---|---|
| oMLX | 不作为默认框架，见上述体积与重复职责原因 | 性能一定差、没有音频能力 |
| ComfyUI | 不默认装；仅复杂节点工作流确实无法由现有接口实现时再评估 | 所有模型都比CPP慢 |
| LocalAI | 不再增加另一套一体化推理管理系统 | 功能弱或不能跑Mac |
| 普通 Docker 容器跑 Mac GPU 推理 | 不采用；推理引擎原生运行，避免Linux虚拟机与Metal/MLX路径问题 | 所有Docker产品都绝无Mac GPU例外 |
| Kubernetes / Docker Swarm | 单台Mac初始阶段不引入 | llama-swap就是Docker Swarm |
| 另加 mlx-audio / mlx-Yue 服务 | 本次不增加；仅保留选定引擎可消费的模型格式 | 原始权重都能被现有引擎加载 |

## 3. 资源编排：让服务可以共存，但任务不要打架

### 两层管理不能争抢

- llama-swap 管外部服务进程的启动、健康检查、HTTP代理及停止。
- 独立 llama-server 固定一个本地 GGUF，交给 llama-swap 控制进程；初期不再开启它自己的多模型路由，避免双重管理。
- MLX-Serve 管自己内部的模型加载/卸载；audio.cpp 的多模型配置也会保持模型，不能被外层错误当作单模型。
- 初期建议每个外层受管实例限制为一个明确模型/功能；如使用综合多模型实例，外层规则必须按**整个实例最大占用**设计。
- 不把所有后端都配置为可用整机256GB。初始为系统和交互应用留32–48GB是保守策略建议，不是实测硬件极限。

### llama-swap 的互斥规则

当前配置由 `routing.router.use` 选择 `group` 或 `matrix`。group里：

- `swap: true`：同组最多一个成员驻留。
- `swap: false`：允许同组共存，不自动预加载所有成员。
- `exclusive: true`：启动该组成员时驱逐其他非persistent组。
- `exclusive: false`：我启动不驱逐其他组，不代表其他组不能驱逐我。
- `persistent: true`：保护该组不被其他组驱逐，不取消组内swap，不等同开机加载。

matrix声明允许共存的组合，例如“大模型+ASR”或“音乐+ASR”，视频独占。它不是根据实时可用RAM自动推导组合的算法。

### 异步任务是关键边界

普通HTTP请求执行期间可以被在途计数保护；**提交音乐/视频后返回202或job ID，不表示后台生成结束**。如果没有任务生命周期适配，服务可能被误判空闲并卸载。需要：

1. 接收任务后记录job ID、目标后端、状态和资源占用资格。
2. 持续轮询后端任务状态，到成功/失败/取消后才释放资源。
3. 默认重任务串行，确认峰值后才放开并发。
4. 不用管理端手动unload作为安全排空手段；该路径可能中断在途请求。
5. `globalConcurrencyLimit`超限返回429，不是持久任务队列。
6. Agent先完成生成工具调用的LLM请求，再申请音乐/视频任务，避免LLM一边占锁等待工具、一边阻塞工具加载。

本包不冒充已实现完整的持久调度器。长期音视频任务建议之后加一个小型本地任务网关，SQLite存任务，文件系统存结果；先不用Redis或集群。独立引擎不互相硬编码调用，由Agent/网关按任务顺序串联。

来源：[配置](https://github.com/mostlygeek/llama-swap/blob/main/docs/config.example.yaml)、[在途跟踪](https://github.com/mostlygeek/llama-swap/blob/main/internal/router/base.go)。

## 4. 满精度选择原则

“最大的”应解释为：用户指定家族中，能力完整且在256GB Mac有合理运行空间的最高精度可用版本，不是仓库文件越大越好。

- 首选BF16/FP16完整权重；保留所有必需编码器、VAE/音频codec、tokenizer、配置和索引。
- 官方本来以低比特发布的版本、转换包8bit、Turbo蒸馏版分别标注，不写成BF16满血。
- 只改变存储布局、保持16位数值的转换，与4/8bit量化不同；两者都需对应引擎格式。
- 视频分辨率、帧数，音乐时长，LLM上下文和并发都会改变峰值。不承诺“权重放得下就一定能跑最高参数”。
- MLX-Serve当前某些M5媒体加速可启用有损W8A8；要做高精度基线，应核对并关闭相关有损路径，不能因为下载BF16就声称全程BF16。


菜单只分 **LLM、音频、图像/视频** 三组；只展示完整模型选项，必需依赖自动加入，技术信息收进 `--details`。共享根目录只询问一次，默认 `~/AI/models`。

| 组别 | 下载选项 | 格式与边界 |
|---|---|---|
| LLM | Qwen3.8 Flash-Next 8bit、27B BF16 | MLX-Serve 路径；前者约194.90GB，后者 BF16 + MTP，下载与最终占盘均约55.59GB |
| 音频 | Music3 MLX 8bit、YuE2 GGUF BF16/F32、Qwen3-ASR | Music3 为 MLX-Serve 完整包；Qwen ASR 与 YuE2 为 audio.cpp GGUF；Qwen 配套内嵌，YuE2 配套外置，均仍需实机验收 |
| 图像/视频 | Qwen-Image-2.1-Turbo、Krea2 Turbo BF16、H3 FL2VA / REF2VA 8bit | Qwen 与 Krea2 为 sd.cpp 完整配套；H3 为 MLX-Serve 完整包，不是 BF16 |

Music3 已核实魔搭完整 MLX-Serve 8bit 包约13.55GB。H3 FL2VA 完整包约70.08GB、REF2VA 约69.30GB，后者支持多模态参考，不含前者的 Turbo LoRA；两版不能混用主权重。YuE2 已核实 audio.cpp GGUF BF16 主模型与 F32 VAE 完整套装，约7.79GB，包含必需外置配套文件；仅限非商用许可。

Qwen-Image-2.1-Turbo 三组件 BF16 约32.44GB，原生 RGBA；已核验 sd.cpp 文档支持 Turbo 与 alpha，未 Mac 实测。Krea2 Turbo 单套约35.41GB，为组件磁盘统计，不是峰值内存。只下载 Turbo 主干及其完整依赖，不再提供 Raw。没有用户 Mac 加载或性能实测。

LLM正式采用 MLX-Serve + 独立 llama.cpp 双格式路径。经用户选择，新增两个独立可选下载项，但不默认下载任何 LLM。原有兼容 GGUF 通过 llama-server 接入，MLX 模型交给 MLX-Serve。GGUF 只是容器格式，不保证任意架构和媒体权重都可互换。


详细来源、许可、组件路径与适配核查见 [MODELS.md](MODELS.md)。

## 5. 验收要求

安装与下载脚本仅完成获取，不替代实测。正式启用前逐项检查：

- 记录程序版本、模型仓库revision、格式、精度和许可证；不要仅记显示名。
- 用同一实际素材对照输出质量；检验中文歌词/字幕、语音自然度、图像文字/构图、H3时序与音画同步。
- 记录冷启动、热启动、任务耗时、峰值统一内存、swap增长以及生成期间UI响应。
- 长任务未完成时提交冲突任务，确认排队或明确拒绝，而不是杀进程丢结果。
- 卸载后确认没有子进程继续持有模型；不要只看HTTP健康端点。
- 同时验证下载包体积、安装目录体积、权重目录体积和缓存目录体积，分别报告。
- 禁止以README宣传、代码编译通过或数学等价证明替代实际观感和硬件测试。

## 追加：Hermes CLI 与安装分层

环境安装脚本只负责基础环境；`setup_ai_capabilities.sh` 负责六组件，默认无联网预览，只有 `--apply` 才执行安装。已有 PATH 命令默认保留，明确标记“版本未验证”，不擅自升级。新的原生组件采用固定官方资产与 SHA256，完整保留动态库和资源。Hermes 固定官方 v0.22.2 提交 `d923a5eb487a17c0a68801bba9ef0f56c81040a8`，最后执行经哈希核验的官方安装器。

使用官方支持的 `--non-interactive --skip-browser --skip-computer-use`，不传 `--include-desktop`。这会跳过 provider setup 与 gateway 安装，并保留 browser/CUA 的 opt-out；不等于只安装一个无依赖 CLI 文件。官方安装器会准备 Python 3.14/uv 与依赖，产品阶段可能取得自身锁定的 Node/工具链和构建 TUI/web，并写入 shell PATH 配置。它不会因为系统已有 fnm Node 就保证完全复用，也不是通过删依赖“精简”到未知状态。

安装器本身没有自定义修改 shell 启动文件，但被调用的 Hermes 官方安装器有 PATH 写入行为，已在预览阶段披露；不绕过该官方流程，也不在这里执行下载代码。所有运行、包体、工具权限与实际模型适配仍待用户 Mac 验证。Hermes 的终端/文件工具能改变用户文件，首次使用前应在 `hermes setup` 中检查 provider、工具权限与执行目录。不要将 llama-swap 的启动能力与 Hermes 的工具自动执行权限混为一谈。

来源：[固定版安装器](https://github.com/NousResearch/hermes-agent/blob/d923a5eb487a17c0a68801bba9ef0f56c81040a8/scripts/install.sh)、[固定版 Python 依赖](https://github.com/NousResearch/hermes-agent/blob/d923a5eb487a17c0a68801bba9ef0f56c81040a8/pyproject.toml)、[发行版](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.2)。

## 统一 Bash 入口、桌面 App 与共享权重根

本版以 `bash start.sh` 为入口。MLX-Serve 只安装桌面 App，用户手动启动、加载模型并开启 API；不配置 MLX CLI。llama-swap 完全可选，不是 Benchmark 依赖。程序和权重分开，模型目录持久保存。

GUI 写入的是官方 Default folder 设置，而非我们另建工具包配置；其他后端继续以各自官方命令或配置引用共享根中的兼容文件。不能把共享目录等同于所有模型格式通用，也不自动替已有后端修改路径。外部盘断开时 MLX-Serve 自身可能回退内置模型目录，必须启动后确认；本包没有修改上游代码来禁止回退。详情见 [共享模型目录](SHARED_MODELS.md) 与安装说明。

## 新增可选 LLM 权重（不默认下载）

- `qwen38-flash-next-8bit`：魔搭 ddalcu/Qwen3.8-Flash-Next-MLX-Serve-8bit，完整194.90GB，含内嵌MTP及57.60GB n-gram表。主干8bit/group64、PLE8bit/group32，是BF16转affine8，不是官方FP8原样；专用MLX-Serve布局。
- `qwen38-27b-bf16`：自动包含 mlx-community BF16 MTP。主模型原目录和文件保持不变，逐文件校验复用；副头只加 `mtp.` 键名前缀以适配固定 MLX-Serve v26.10.2，张量字节不变。下载与最终占盘均约55.59GB；运行时在桌面 App 核对注意力量化与 MTP 设置，不因 BF16 文件就假定执行精度。未在 Mac 实测，详见 MODELS.md。

两项互相独立、没有被其他选项自动依赖，只有用户主动选择才下载。共享根目录采用 `llm/<model>` 两层；GUI识别目录不等于推理兼容性验收。两者均保存文件大小/SHA256快照，master可变，建议实际下载时加 `--sha256`。这次没有下载模型、安装软件或在Mac推理。上述GB仅是磁盘统计，不能代替256GB机器的峰值内存评估。详见 [来源与边界](MODELS.md#可选-qwen38-llm-下载项)。

## H3 双版本与磁盘复用

FL2VA 用于首尾帧，REF2VA 用于参考图像/视频/音频。v26.10.2 非 REF2VA 请求 ref_audios 会返回400，不能把 LTX audio 字段用于此处。两套按完整 SHA256 比较共有约34.05GB文件，完整磁盘统计由约139.37GB降为唯一文件约105.33GB，不代表运行内存减少或并发安全。任意单选或双选都会检查当前模型根的两版清单目录及旧 video/h3-mlx-8bit，只链接 SHA256 相同文件，只补缺失或不同文件。不全盘扫描，外部目录才使用手动复用入口；来源保留，不搬改删。原仓库目录名与大小写不改，详见 [H3版本、来源与音频示例](H3.md)。
