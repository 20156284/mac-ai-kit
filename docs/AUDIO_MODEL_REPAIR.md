# 音频模型识别与补齐

已有模型不用删除。更新工具包后，沿用原模型目录，在「下载模型」重新选择 YuE2：先核对已有两份 GGUF 的 SHA256，仅补缺失文件；再从服务菜单启动 audio.cpp。

- Qwen3-ASR：清单已更新到 4,087,660,800 字节，同时保留已核验旧版 4,087,653,248 字节的兼容识别。下载复用须匹配同一版本的大小与 SHA256，不以大小代替内容验证。
- YuE2：两份 GGUF 之外还有四个 sidecars 文件，总计约 2.56 MB；若只缺其中三个，则只下载三个。目录有权重不等于附件齐全。
- 服务诊断只检查文件元数据，不自动下载模型。一个音频模型齐全即可配置；另一个不齐不会阻止可用模型启动。文件检查不等于推理测试。

## 核验依据（2026-10-25）

[ASR 魔搭文件树](https://modelscope.cn/api/v1/models/HereIsMark/audio.cpp-gguf/repo/files?Revision=master&Root=Qwen3-ASR-1.7B-GGUF&Recursive=true)：提交 `8a9bd3309e2222b1b2a3fb2ca436f5a32d284b7b`，当前文件 SHA256 为 `59baf62f2454a1b4d1d9ffed730dbf1d0bce28ed6fec30897de17f9d6d452fc3`。旧版允许值见 `catalog.json`。

[YuE2 魔搭仓库](https://modelscope.cn/models/audio-cpp/Yue2-3B-GGUF)：两份 GGUF 元数据仍与清单一致。以下小文件已实际读取并重算摘要，均匹配清单：

- `sidecars/yue2-model-config.json`：959 字节。
- `sidecars/yue2-generation-config.json`：466 字节。
- `sidecars/yue2-qwen.tiktoken`：2,561,218 字节。
- `sidecars/yue2-vae-config.json`：1,378 字节。

本次没有下载模型权重或运行推理。回归测试使用小型夹具验证只补组件、保留原权重以及旧版摘要配对；不代表 Mac 实机推理验证。
