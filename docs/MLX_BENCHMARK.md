# MLX benchmark

主菜单第 5 项 **Benchmark → 第 2 项 MLX-Serve 长上下文 benchmark**，提供 0、50K、100K、200K 四档长上下文测试。第一项 Llama benchmark 保留作通用接口测试；其 MLX-Serve PP 计时不准，不应混为同一测量口径。

```bash
bash start.sh mlx-benchmark
bash start.sh mlx-benchmark --base-url http://127.0.0.1:11234/v1
bash start.sh script-help mlx-benchmark
```

先在 MLX-Serve App 中开启 API。默认连接 `http://127.0.0.1:11234/v1`；实际端口以 App 显示为准，可在提示处填写完整地址。不读取 llama-swap 的代理端口，不需要启动代理。只测试已有接口，不安装依赖、不下载模型权重、不启动或停止服务。选择本入口并指定接口/模型后即开始四档测试，不额外询问 yes。运行前请检查目标模型与上下文容量；若接口是按需加载模型的代理，推理请求仍可能触发它加载模型。

也可直接使用随包脚本：

```bash
python3 scripts/mlx_benchmark.py --help
python3 scripts/mlx_benchmark.py --base-url http://127.0.0.1:11234/v1
```

默认报告目录为项目内 `output/reports/mlx-benchmark-<UTC时间戳>-<随机后缀>/`，不受启动时所在目录影响；`.gitignore` 已忽略 `/output/`。可用 `--output` 指定本次报告目录，旧报告不会搬移或删除。

背景深度 0 / 50,000 / 100,000 / 200,000，各加 2,048 输入预算；目标内容总长 2,048 / 52,048 / 102,048 / 202,048 tokens，聊天模板另计，以实际 prompt_n 为准。输出上限改为 480 tokens，与现有 Llama Benchy --pp 2048 --tg 480 的预算对齐。每档先生成预热 1 次，再正式测量 1 次，不自动重试生成。脚本会产生 `results.csv`、`results.json` 和可离线打开的 `report.html`。保留失败与不完整结果，不将未执行的档位当成零分或成功。PP/TG 采用服务端指标；首 token 显示为服务端 tokenize_ms + prompt_ms 的估算值，不是真实网络 TTFT。吞吐数值必须连同来源、缓存和 token 计数一起解读，不可用首 token 等待时间冒充纯 PP 时间。

高上下文请求可能占用大量内存和较长时间；50K、100K、200K 是测试档位，不保证后端接受完整输入。使用不可信或收费接口前先核实服务与成本。报告包含模型与接口等信息，分享前检查。

验证范围仅为离线模拟、参数和菜单回归。未在用户 Mac 运行，未下载权重或执行真实模型推理，未宣称 200K 性能实测通过。


## 原理、预热与比较边界

1. 从 `/v1/models` 选择现有模型，读取 `/props` 的容量与配置快照；通过 `/tokenize` 校准自定义记录文本（含检查标记），不下载模型；此分词步骤本身不算生成预热。随机前缀降低跨档前缀缓存复用，最终 `cached_n` 才是证据。分词不可用时明确标记字符估算，不能当精确对齐。
2. 每档仅发送一次非流式 `/v1/chat/completions`，读取 `timings.prompt_n/prompt_ms/prompt_per_second` 与 `predicted_n/predicted_ms/predicted_per_second`。PP 包含服务自身的缓存查找/恢复及未缓存 forward 计时，不等于客户端端到端速度。TTFT 仅 `tokenize_ms + prompt_ms` 的估算，不含全部排队、网络及首个实际 token 等开销。
3. 480 是上限，不保证模型一定输出 480。提示要求持续生成；仍记录实际输出与 `finish_reason`，不强制重试或伪造数量。少于/超过 480 的原始 TG 保留，但不纳入最佳值和曲线。单次不能计算稳定性或有意义的标准差。
4. 每档先生成预热一次，不纳入正式结果；随后正式测量一次。预热和正式输入采用不同前缀，并分别校准长度。首次 GPU 内核编译、分配、页驻留等可能影响结果；排队和模型加载不一定落在服务 PP 计时范围内。分词不是 GPU 预热。重复相同提示会命中 KV 前缀缓存，与硬件预热不同；本报告只有 cached_n=0 的成功样本进入 PP 摘要。
5. MLX-Serve 26.10.3 内置套件是先短校准，每档一次预热、两次 coding 取中位数，另有 counting ceiling 与首档复测 drift，输出上限 192。本脚本只使用同一原生计时字段，采用不同语料、480 输出、每档一次，不是原套件的等价扩档。未做 ceiling、drift、重复测量的字段保持“未测”，不补造数值。
6. 与 Llama Benchy 对比必须统一模型精度、MTP/PLD、KV、实际输入输出长度、并发及热状态。输入预算和输出上限一致仍不保证语料、采样、缓存及计时口径一致。旧 Llama Benchy 的流式首消息可能只是空角色事件，不能用它推算 MLX-Serve 的纯 PP；本项读取原生 timings 避开该口径问题。完整运行共 8 次生成（4 次预热、4 次正式）；稳定性研究仍需更多正式重复，但本版不自动增加轮数。

来源：[MLX-Serve 原生测试流程](https://github.com/ddalcu/mlx-serve/blob/v26.10.3/app/Sources/MLXServe/Services/BenchmarkRunner.swift#L75-L219)、[套件参数](https://github.com/ddalcu/mlx-serve/blob/v26.10.3/app/Sources/MLXServe/Models/BenchmarkModels.swift#L264-L281)、[llama-benchy 计时](https://github.com/eugr/llama-benchy/blob/v0.4.0/src/llama_benchy/results.py#L259-L296)。

### Llama Benchy 的预热不等于 runs

llama-benchy v0.4.0 默认每个组合实际运行 `runs + 1` 次，第一轮是丢弃的预热；旧命令未加 `--no-warmup`，因此 `--runs 1` 是一次正式结果加一次整档预热，不是总共一次生成。另有模板和延迟校准短探针。本 MLX 脚本每档也预热一次，但语料、额外短探针及服务端计时不同，不能称完全同题。预热原始结果保存在 results.json 的 warmups，不进入正式 CSV/HTML；预热与正式输入使用不同随机前缀，避免整段前缀缓存污染。预热失败或超时即停止，不重试。见 [runner.py](https://github.com/eugr/llama-benchy/blob/v0.4.0/src/llama_benchy/runner.py#L92-L95)。
