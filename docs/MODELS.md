# 模型清单、精度与兼容性核验

核验日期：2026-10-20。仅文档、模型卡、许可证及有限文件头检查，未下载完整权重、运行或生成媒体。256 GB 为统一内存，不是模型磁盘额度；下述磁盘数字不得直接当峰值内存。主范围为 Qwen-Image-2.1-Turbo、Krea2、Music3、YuE2、H3；语音仅保留 Qwen3-ASR 转写。


## 当前菜单范围

菜单只分 **LLM、音频、图像/视频** 三组；只展示完整模型选项，必需依赖自动加入，技术信息收进 `--details`。共 10 个模型选项：2 个 LLM、4 个音频、4 个图像/视频。共享根目录只询问一次，默认 `~/AI/models`。

| 组别 | 下载选项 | 格式与边界 |
|---|---|---|
| LLM | Qwen3.8 Flash-Next 8bit、27B BF16 | MLX-Serve 路径；前者约194.90GB，后者 BF16 + MTP，下载与最终占盘均约55.59GB |
| 音频 | Music3 MLX 8bit、YuE2 GGUF BF16/F32、Qwen3-ASR | Music3 为 MLX-Serve 完整包；Qwen ASR 与 YuE2 为 audio.cpp GGUF；Qwen 配套内嵌，YuE2 配套外置，均仍需实机验收 |
| 图像/视频 | Qwen-Image-2.1-Turbo、Krea2 Turbo BF16、H3 FL2VA / REF2VA 8bit | Qwen 与 Krea2 为 sd.cpp 完整配套；H3 为 MLX-Serve 完整包，不是 BF16 |

Music3 与 H3 已核实魔搭完整 MLX-Serve 8bit 包，分别约13.55GB和70.08GB。YuE2 已核实 audio.cpp GGUF BF16 主模型与 F32 VAE 完整套装，约7.79GB，包含必需外置配套文件；仅限非商用许可。

Krea2 Turbo 单套约35.41GB，为组件磁盘统计，不是峰值内存。只下载 Turbo 主干及其完整依赖，不再提供 Raw。没有用户 Mac 加载或性能实测。

## LLM：正式加入独立 llama.cpp

- 后端分工：本地、已确认兼容的 GGUF LLM 使用独立 `llama-server`；MLX 格式使用 MLX-Serve。llama-swap 统一启停，但不转换模型。
- 在用户明确选择后，新增下述两个独立 LLM 下载选项；安装器、list 和 dry-run 均不自动下载，选一项不会连带下载另一项。
- 仍优先原精度 F16/BF16，但须先确认所选模型架构、GGUF转换、当前版本及Metal硬件实际支持。Q8/Q4 是量化，不可写成原精度；文件名带 BF16 也不能代替模型元数据核验。
- GGUF 不是万能通行证：本目录中的图像文本编码器、audio.cpp GGUF，不自动成为可聊天的 llama.cpp 模型；视觉模型还可能需要匹配的 projector，不能随意配对。
- 256GB预算还需容纳上下文KV、并发和运行缓冲。安装示例从4096上下文、单slot开始，属于保守起点，不是已测上限或吞吐承诺。
- llama.cpp 程序为 [MIT](https://github.com/ggml-org/llama.cpp/blob/v0.7.0/LICENSE)；每个模型的原许可仍独立适用。

来源：[官方项目](https://github.com/ggml-org/llama.cpp)、[固定版本server文档](https://github.com/ggml-org/llama.cpp/blob/b11669/tools/server/README.md)。

## 可选 Qwen3.8 LLM 下载项

核查于 2026-10-20，仅获取公开文件清单、config、index 和模型卡，没有下载权重或实机加载。两个条目均需用户手动选择；不设默认，不作为其他模型依赖。共享路径均为 `llm/<model>` 两层，便于 MLX-Serve GUI 扫描。下载器不会启动引擎或启用 MTP。

### qwen38-flash-next-8bit

- 来源：[ddalcu/Qwen3.8-Flash-Next-MLX-Serve-8bit](https://modelscope.cn/models/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-8bit)。完整仓库 194,900,880,527 bytes（194.90GB，十进制）；101 个 safetensors（100 个编号主分片及视觉塔），加 `ngram_table.bin` 57,600,276,856 bytes。MTP 的 `language_model.mtp.*` 已在主分片索引中，不因没有独立 mtp 文件而遗漏。
- 主干 affine 8bit/group64；n-gram（PLE）8bit/group32，视觉/部分结构权重保留 BF16。这是从 BF16 转换、重新量化的 MLX-Serve 包，**不是官方 FP8 权重原样保留**，也不是主干8bit/PLE4bit的混合包。
- 针对 ddalcu/mlx-serve 专用布局，不能机械套用普通 mlx-lm 或别的引擎示例。n-gram 可由该引擎 host mmap/SSD 按需读取；统一内存中的 CPU/GPU 并不是两份独立 RAM。文件总量不能当作驻留内存或峰值；KV、并发、系统和加载缓冲另计。没有本机256GB速度/峰值验证。
- 已比对同名 Hugging Face 源仓库：103 个 LFS 文件的大小及 SHA256 一致，包括所有权重与 n-gram；两个平台的辅助文件有差异，因此不声称整仓逐文件完全相同。
- 模型许可为 Qwen Community License 1.0，独立于工具包 MIT，下载前阅读仓库 LICENSE。

### qwen38-27b-bf16

- 来源：[mlx-community/Qwen3.8-27B-bf16](https://modelscope.cn/models/mlx-community/Qwen3.8-27B-bf16)。实际文件清单已核实，54,743,807,404 bytes（54.74GB），11 个主分片及 config/tokenizer/视觉处理配置。模型卡声明从官方 Qwen3.8-27B 通过 mlx-vlm 0.6.8 转换，BF16 MLX；模型卡许可 Apache-2.0，仍应核对原模型条款。
- 主仓库本身不含 MTP，所以这个选项自动补齐 [mlx-community/Qwen3.8-27B-MTP-bf16](https://modelscope.cn/models/mlx-community/Qwen3.8-27B-MTP-bf16)，不是另一个菜单项。保留原主模型目录；已有文件按 SHA-256 校验复用，仅下载缺失部分，损坏文件不擅自覆盖。
- MTP 原文件 `model.safetensors` 为849,400,335 bytes，15个 BF16 张量。下载器生成 `mtp/weights.safetensors`（849,400,400 bytes），仅在 safetensors 头部给键名加 `mtp.`，原始张量数据逐字节复制，既不量化也不重新执行 norm +1。新安装仅在 `mtp/` 保留最终 `weights.safetensors`；源文件暂存、核验、适配成功后清理。下载与最终占盘均约55.59GB。只补 MTP 时最终新增约0.85GB；首次处理峰值约1.70GB。已有最终普通文件且大小匹配即跳过，不要求原件、不做全量哈希；这不是推理验证。旧版本留下的原件不再需要，但脚本不擅自删除已有文件。
- 这是本地格式适配，不是声称社区原包可直接运行。[v26.10.2 MTP loader](https://github.com/ddalcu/mlx-serve/blob/v26.10.2/src/mtp.zig) 接受该 sidecar 路径和前缀；社区[拆分脚本](https://github.com/Blaizzy/mlx-vlm/blob/main/mlx_vlm/speculative/drafters/qwen3_5_mtp/split.py)已对相关 norm 折叠 +1，loader 自动检测，因此适配器不能再次 +1。已核查结构，尚未在 Mac 实测加载、输出与加速。
- MLX-Serve 使用桌面 App 手动加载；基线测试前核对 App 的注意力量化、KV 和 MTP 设置及加载日志，文件为 BF16 不保证所有运行路径均为 BF16。本包不配置 CLI 或修改运行精度。
- 文件容量不代表实际内存，仍需留出上下文 KV、视觉输入、并发和系统空间；本次不承诺 Mac 上即用、峰值或速度。

### 快照与校验

两个选项使用可变 `master`，不是固定不可变 commit。`catalog.json` 明确列出当前下载文件，并对每个文件记录字节数及 SHA256（包括 config/index）。默认下载后检查存在与大小，传 `--sha256` 才会计算和比较哈希；约250GB合计读取会耗时。上游变化导致失败时需先重新核查更新，不能直接跳过。两个仓库的 [Flash-Next files API](https://modelscope.cn/api/v1/models/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-8bit/repo/files?Revision=master&Recursive=true) / [27B files API](https://modelscope.cn/api/v1/models/mlx-community/Qwen3.8-27B-bf16/repo/files?Revision=master&Recursive=true) 为快照来源。只有 Flash-Next 做了 HF 全部 LFS 对照，不把此结论泛化到27B。

## 图像


### Qwen-Image-2.1-Turbo：原生 RGBA 透明背景

官方 [Qwen/Qwen-Image-2.1-Turbo](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo) 的 Turbo 主干，由 [Comfy-Org 魔搭仓库](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1) 提供 sd.cpp 可读的 BF16 拆分文件。菜单排在 Krea2 前，一次选择配齐以下三项，不下载普通版、量化版或额外加速 LoRA：

- `diffusion_models/qwen_image_2.1_turbo_bf16.safetensors`：14,230,280,616 字节。
- `text_encoders/qwen3vl_8b_bf16.safetensors`：17,534,334,616 字节。
- `vae/qwen_image_2.1_vae_bf16.safetensors`：675,509,688 字节；必须用 2.1 专属 VAE，不能换成旧 Qwen 或 Wan VAE。

统一保存在 `images/Qwen-Image-2.1/`，保留上述原始文件名及路径。三项权重共 32,440,124,920 字节（约 32.44 GB），不含小型说明文件、缓存与运行开销；不是峰值内存。已核对魔搭与同名 HF 拆分文件的大小和 SHA256，未下载完整权重或在 Mac 推理。

已核验 sd.cpp `master-1006-0886f11` 的[官方文档](https://github.com/leejet/stable-diffusion.cpp/blob/master-1006-0886f11/docs/qwen_image_2.1.md)已包含 Turbo 与 alpha 输出；当前安装改为官方最新发行包。该文档要求 Turbo sigma 预设、8 步、CFG 1、Euler；宽高为 32 的倍数。透明背景为模型原生 RGBA 能力，不是生成后抠图；提示词需明确透明背景，保存 PNG/WebP 才保留 alpha，JPG 不保留。未承诺 Mac 实际速度或质量。

权重遵循 [Qwen Research License](https://modelscope.cn/models/Qwen/Qwen-Image-2.1-Turbo/resolve/master/LICENSE)：仅限非商业研究或评估，商用需另获许可，不是任意非商业用途均可，也不是 Apache/MIT。镜像和格式转换不解除原许可限制。魔搭 `master` 可变，下载完整性依据清单中的文件大小与 SHA256；不是固定整仓提交。

### Krea2

本包只保留 **Krea 2 Turbo safetensors BF16（图像）**，低步数效率版；不再提供 Raw，不把 Turbo 称作未蒸馏原版。主干和文本编码器采用已核实的 [Comfy-Org/Krea-2 魔搭拆分文件](https://modelscope.cn/models/Comfy-Org/Krea-2/tree/master/)，仅取所需精度，不整仓下载量化或其他变体。

sd.cpp 官方 Krea2 文档要求 Krea2 diffusion transformer + **Qwen3-VL 4B 文本编码器** + **Wan2.1 VAE**，支持 safetensors 与 GGUF。上游拆分仓库当前文件：
- diffusion_models/krea2_turbo_bf16.safetensors：26.3GB
- text_encoders/qwen3vl_4b_bf16.safetensors：8.88GB
- VAE 取 sd.cpp 官方文档指向的 wan_2.1_vae.safetensors。

以上 DiT/编码器路径已由魔搭官方 files API 进一步验证。Krea仓库自带VAE名为qwen_image_vae.safetensors；为了遵循sd.cpp文档，应另取 Comfy-Org/Wan_2.1_ComfyUI_repackaged 的 split_files/vae/wan_2.1_vae.safetensors，253,815,318字节。约35GB核心权重不等于运行峰值；256GB 可优先高精度，留系统与激活余量。此处未做 Mac 峰值或速度实测。

来源：[sd.cpp Krea2 加载说明](https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/krea2.md)、[DiT 文件](https://huggingface.co/Comfy-Org/Krea-2/tree/main/diffusion_models)、[文本编码器文件](https://huggingface.co/Comfy-Org/Krea-2/tree/main/text_encoders)。

许可是 **Krea 2 Community License**，不是 Apache/MIT。原始 HF 仓库需要同意许可。开源下载器可独立开源，不代表可以把模型改标为同一许可、绕过门控或直接再分发。应保留来源/许可链接，由使用者处理必要许可流程。

来源：[Krea 原始模型及许可入口](https://huggingface.co/krea/Krea-2-Turbo/tree/main)。

## 音乐与视频：现成 Mac 格式与来源缺口

2026-10-20 进一步读取魔搭文件树、README 与配置，核实以下完整包。这里只声明来源和目标格式，不声称已在用户 Mac 加载成功。

- Music3：[ddalcu/MiniMax-Music3-MLX-Serve-8bit](https://modelscope.cn/models/ddalcu/MiniMax-Music3-MLX-Serve-8bit)，完整13,552,904,390字节，含五个模型组件、配置及tokenizer。面向 ddalcu/mlx-serve 原生 Apple Silicon 路径，8bit 而非 BF16；保留包内更高精度组件，不自行降精度。
- H3：[ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit](https://modelscope.cn/models/ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit)，完整70,076,153,374字节，包含779,849,816字节 Turbo LoRA、专用文本编码器、tokenizer、音频与视频 VAE。affine8/group64 与部分高精度组件混合，不称全 BF16。H3 的地域与商用许可限制仍适用。
- YuE2：[audio-cpp/Yue2-3B-GGUF](https://modelscope.cn/models/audio-cpp/Yue2-3B-GGUF)，选择 BF16 主模型（7,261,484,576字节）、F32 VAE（530,537,760字节）、四个 `sidecars/yue2-*` 配套文件及 README，完整7文件共7,794,596,343字节。不是需自行转换的原始包，也不是量化主模型。README 明确对应 audio.cpp 原生实现，已核对 GGUF 文件头的 yue2 家族与 spec version 1。四个配套文件是外置必需项，不能只下载两个 GGUF；下载器自动包含模型配置、生成配置、qwen.tiktoken 和 VAE 配置。使用支持 yue2 的 audio.cpp 版本，首次加载仍需验收；文档中的 CUDA 测量不能套用到 Mac，未做本机生成测试。权重为 CC-BY-NC4.0，仅限非商用。

Music3 和 H3 使用完整转换包替代旧原始素材入口，不要求用户自行转权重，不静默切换下载平台。文件大小是磁盘统计，不是峰值统一内存，也不保证所有生成规格可运行。

## 许可证必须分模型说明

下载交互不再要求重复输入 yes 或接受许可；以下条款也可通过 `--details` 查看。若平台另需登录或接受条款，应由用户自行在平台完成；本工具不自动接受门控条款或绕过访问限制。

- Music3：[官方许可证原文](https://huggingface.co/MiniMaxAI/MiniMax-Music3/raw/main/LICENSE)。自定义社区许可，不是MIT/Apache。商用UI署名、相关产品/服务年度合计收入超过2000万美元另获书面授权、对第三方开放生成服务须有安全保障，保留许可证与AUP。许可证日期字段不直接当正式模型发布日期。
- H3：[官方许可证原文](https://huggingface.co/MiniMaxAI/MiniMax-H3/raw/main/LICENSE)，2026-08-02。适用地域排除欧盟、英国、韩国、美国，约束还覆盖输出；收入门槛与商用署名、托管保障另有规定。条款保留在本文与 `--details`，不再弹出许可确认；不推测用户地理位置，不把可下载当作许可适用。
- YuE2：[官方模型卡](https://huggingface.co/m-a-p/YuE2-3B)明确权重CC-BY-NC4.0；可下载不代表可商用。开源下载器的MIT许可不改变权重许可。
- Qwen ASR：模型卡Apache2.0。代码许可与权重许可分开。

## 下载清单规则

- 菜单仅保留现有 Mac 引擎对应格式的已核查下载目标；不展示未核实镜像的占位项；下载格式不等于已通过 Mac 运行验收。
- 完整模型自动包含所需组件；仓库、精度、文件与依赖可通过 `--details` 查看。
- 来源核查不代替实际加载验收。audio.cpp 已核对内嵌 spec 和配套文件清单；安装版 loader 与 MLX 版本仍需实际加载验收。
- 不运行模型仓库代码，不自动转换、安装另一套推理框架或生成媒体。

## 语音识别下载选项

- Qwen3-ASR-1.7B：audio.cpp GGUF F16，约 4.09 GB；来源仍为 HereIsMark/audio.cpp-gguf，本地直接放入 `audio/Qwen3-ASR-1.7B-GGUF/`，不套仓库名外层。已核对 GGUF 头、模型规格与内嵌附件，未 Mac 实测。

语音对齐、TTS Base、CustomVoice、VoiceDesign 已从下载清单移除；不会删除用户已有文件。音乐模型保留。

## 完整包目录

Qwen 完整三组件在 `images/Qwen-Image-2.1/`。Krea2 主干与编码器统一保存在 `images/Krea-2/`，VAE 在其 `dependencies/Wan_2.1_ComfyUI_repackaged/` 下。全部保留仓库内文件路径及大小写，必需依赖自动配齐，不让用户逐项挑选。

详情见 [共享模型目录](SHARED_MODELS.md)。更改布局不会自动搬迁旧版文件，不要把旧版缓存存在当作新版路径已就绪。

音乐权重统一保存到共享根的 `music/`：YuE2 为 `music/Yue2-3B-GGUF/`，Music3 为 `music/MiniMax-Music3-MLX-Serve-8bit/`；Qwen3-ASR 语音转写权重放 `audio/`。菜单仍归“音频”组。旧目录不自动迁移或删除；H3 单选或双选均自动检查当前根内两版清单目录及旧 `video/h3-mlx-8bit`，完整 SHA256 一致才链接复用。只下载缺失或不同文件；外部来源才需 r。不全盘扫描、不迁移、不删除，链接来源须保留。

## H3 REF2VA 与文件复用（2026-10-21 补充）

[REF2VA 魔搭完整 8bit 包](https://modelscope.cn/models/ddalcu/MiniMax-H3-REF2VA-MLX-Serve-8bit)已核查，共15文件、69,296,302,685字节，不含 FL2VA 的 Turbo LoRA。两版 transformer 各不相同；共享文件必须依据完整 SHA256 判断，不按同名猜测。REF2VA 提供参考音频/图像/视频条件，不支持首尾帧；FL2VA 则相反。具体版本限制、下载复用及本机音频参考示例见 [H3](H3.md)，不代表 Mac 运行验收。
