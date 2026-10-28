# llama-swap（可选）

MLX-Serve 使用桌面 App：用户手动打开、加载模型并开启 API。安装与 Benchmark 不需要 llama-swap。

如需托管兼容 GGUF LLM，在安装菜单中单独选择 llama-swap，再进入「服务管理」中的「llama-swap（可选）」配置 llama.cpp 路由、启动或停止。登录启动是独立选项。安装 all 不包含 llama-swap。

工具状态、配置和日志位于工具根 `services/llama-swap/`，与共享模型目录分开。只操作本工具包拥有的服务，不接管外部进程。既有配置不会自动扩大监听范围。

旧版 MLX 托管配置只做识别，不再用于启动或登录启动；本包不自动删除旧配置或停止已有进程。请先通过原服务入口确认旧服务已停止，再在桌面 App 加载模型，避免重复占用。

新版默认入口 `bash start.sh services` 管理独立的 SD-Qwen / audio 服务，见 [独立服务管理](NATIVE_SERVICES.md)。llama-swap 的兼容高级入口：`python3 scripts/service_manager.py --models-dir /你的模型目录`。状态检查不加载模型；GGUF 后端仍在首次实际推理请求时按需启动。

来源：[llama-swap v265](https://github.com/mostlygeek/llama-swap/tree/v265)。
