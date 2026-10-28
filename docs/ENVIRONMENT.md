# Apple Silicon Mac 基础环境脚本

核查日期：2026-10-20。独立脚本：`scripts/setup_mac_environment.sh`。

它负责 Homebrew、macmon、FFmpeg、uv、Node 版本管理器与 Docker Desktop，必要时可选准备 uv 托管 Python 3.12，**不安装模型、推理引擎或项目依赖，不启动服务**。交付的是脚本，未在用户的 Mac 上执行安装。

## 使用

在工具包根目录的终端运行 `bash start.sh`，选择基础环境检查或安装。需要 Python/uv 等依赖的任务也会在同一入口中引导准备，成功后接着执行原任务。菜单本身是 Bash，不依赖 Python。

下文描述底层实现与边界，普通使用无需手动运行脚本。仅查看环境不会联网安装或修改 shell 文件；安装逐项询问，默认回答为“不”。

不要用 `sudo bash ...`，不要用 `yes | ...`。安装模式必须在交互式原生 arm64 macOS 终端中运行。预览可在其他系统运行，但显示的是该系统现状，不是你的 Mac 现状。

## 新 Mac 只使用 fnm

- 按本机全新环境方案，只安装 **fnm**，不提供第二种 Node 管理器选择，也不执行独立的 `brew install node`。
- fnm 官方推荐 macOS 经 Homebrew 安装，适合这套 brew 基础环境；Node 使用 24 LTS。
- Homebrew 安装 fnm 不会自动写入 zsh 初始化；本脚本经确认后把 fnm 官方初始化写入 **`~/.zshrc`**，不是 `~/.zprofile`：交互式 zsh 新终端会读取它。脚本会先显示具体内容，再询问是否追加；已有 fnm 初始化不重复添加。不会自动改动当前父终端。
- 意外检测到其他 Node 管理器或其常见 shell 配置时，暂停 Node/fnm 阶段并提示人工检查；不切换、不卸载、不删除旧配置。这是冲突保护，不是保留另一种安装方案。
- Node 固定 **24 LTS 主版本**，由管理器在安装时解析最新 24.x 补丁，不追随 `--lts` 跨主版本变化。截至核查日 Node 24 为 LTS、26 为 Current。没有锁定补丁，因此不是位级可复现环境。
- 已有 PATH 中可用的 Node，或者管理器已有本地 Node 版本时不自动升级、迁移或覆盖；这也意味着旧/EOL Node 不会被自动修复。用 `node --version` 和官方生命周期表核查后，手动决定迁移。

## 执行顺序与边界

1. 检查 macOS、arm64、普通用户、交互式终端和 Xcode CLT。CLT 缺失时，安装流程会询问是否打开 Apple 开发工具安装界面；完成系统界面后在菜单中继续检查，未就绪则停止本次任务。许可或权限仍由用户操作，不代接受条款。
2. 优先使用 `/opt/homebrew/bin/brew`。没有则经确认下载 Homebrew 官方安装器，第二次确认才运行；可能需要本机管理员密码。非标准前缀或 Intel brew 不会被直接用于混装。
3. 分别检查并提示 `brew install macmon`、`brew install ffmpeg`、`brew install uv`。已有命令直接保留；brew 已登记但命令不可用时报告，不强制 relink。
4. 检查 `curl`、`tar`、`shasum`、`git`（macOS/CLT 提供）。PATH 中 Python 低于 3.10 或缺失时，先只读查找已有 Python 3.12，仍缺失则单独确认 `uv python install --no-bin 3.12`；打印实际解释器路径，不创建用户 bin 命令链接、不替换系统 Python 或改 PATH。后续脚本可用 `uv python find --no-python-downloads 3.12` 获取显式路径。仅安装/使用 fnm，需要时安装 Node 24。没有独立执行 `brew install node`。
5. 每一项 shell 初始化单独预览、单独确认。使用官方初始化语句：Homebrew 的 `eval "$(/opt/homebrew/bin/brew shellenv)"` 写入 `~/.zprofile`，fnm 的 `eval "$(fnm env --shell zsh)"` 写入 `~/.zshrc`。保留工具官方默认安装/数据目录，不另建配置树；使用标记防重复，拒绝写符号链接。不会 source 整个用户配置文件，不修改其他 shell 的文件。
6. 检查 `/Applications/Docker.app`、`~/Applications/Docker.app` 和 brew cask 登记；缺失时才提示 `brew install --cask docker-desktop`。不会把已有 `docker` CLI 误判为完整 Docker Desktop。
7. 输出命令路径/版本、Docker 应用位置/版本以及失败、拒绝次数；失败项可修复后重跑，不回滚已成功部分。

Homebrew 的自动更新、清理和分析在**本次进程**禁用，不持久修改用户配置；脚本不执行 `brew upgrade`。但安装新公式仍可能安装/更新必要依赖，不承诺完全隔离或固定 brew 依赖树。ffmpeg 的依赖可能占用较多磁盘空间。

fnm 初始化采用官方 `fnm env` 输出，追加后在未来终端执行；可能让 fnm 默认 Node 优先于其他 Node。脚本明确提示并单独询问，拒绝即可保留原 shell 行为。不启用自动随目录切换。新增配置不会自动影响当前父终端，请打开新终端。

## Docker Desktop 的图形界面与许可

`docker-desktop` 是当前 cask 名，旧名为 `docker`。安装的是 Docker Desktop 图形应用和 cask 声明的 CLI 链接等标准文件，而非只装 Docker 命令行。cask 可能要求管理员权限；遇到 CLI 文件冲突会失败，不用 `--force` 覆盖。系统兼容性以当前 cask/官方安装页为准。

脚本**不打开应用、不登录 Docker、不自动接受许可、不设置开机启动、不配置特权辅助服务**。安装后由用户自行从 Applications 打开，阅读许可与权限界面并选择。未完成首次启动时 `docker info` 失败，不等于安装失败。

Docker Desktop 个人使用、教育、非商业开源及符合条件的小企业可免费使用；小企业条件包括少于 250 名员工且年营收低于 1,000 万美元。较大企业商业用途需付费订阅，其他情形以当前条款为准。请在首次启动前阅读 [Docker 订阅服务协议](https://www.docker.com/legal/docker-subscription-service-agreement/) 和 [Mac 安装说明](https://docs.docker.com/desktop/setup/install/mac-install/)。脚本不代用户接受协议。

## 验收与问题处理

菜单会报告环境检查结果。以下仅供维护者在新终端独立验收（Node 需先选用 fnm 的 24 版本），不是安装前置步骤：

```bash
brew --version
macmon --version
ffmpeg -version
uv --version
node --version
npm --version
# Docker 自行完成首次启动后再检查服务端
# docker version
# docker info
```

- 预览退出码 0 只表示预览完成，不表示依赖齐全；安装中主动拒绝也不算安装错误。
- 退出码 1 表示安装失败/需人工处理或缺 CLT；退出码 2 表示参数、平台或运行方式不符。
- 网络失败：检查官方地址、网络和现有代理，重跑即可；不要关闭 TLS 校验，不自动改 VPN/DNS/安全配置。
- 已有 Node 太旧：脚本有意保留，请先确认项目需求，再通过现有管理器手动安装/切换。
- brew 登记存在但命令失踪：看 `brew info <名称>` 和 `brew doctor`，先查 PATH/链接问题，不盲目强制覆盖。
- 自定义 shell：本机方案只负责 zsh 的 `~/.zprofile`（Homebrew）和 `~/.zshrc`（fnm）；其他 shell 请参考官方初始化方式，不会自动修改。
- 安装不是事务；中断或部分失败后保留现状与提示，重新运行会检查已成功项。仅清理自己创建的临时安装器目录。

## 核查来源

- [Homebrew 官方安装要求和 Apple Silicon 默认前缀](https://docs.brew.sh/Installation)
- [macmon 公式](https://formulae.brew.sh/formula/macmon)
- [FFmpeg 公式](https://formulae.brew.sh/formula/ffmpeg)
- [uv 官方安装方式](https://docs.astral.sh/uv/getting-started/installation/)
- [fnm 官方项目与 macOS Homebrew 安装建议](https://github.com/Schniz/fnm)
- [Node 官方版本状态](https://nodejs.org/en/about/previous-releases)
- [Docker Desktop 当前 Homebrew cask](https://formulae.brew.sh/cask/docker-desktop)
- [Docker Mac 权限说明](https://docs.docker.com/desktop/setup/install/mac-permission-requirements/)

## 测试范围

已做 Bash 语法检查、只读预览、参数拒绝和非 macOS 安装保护检查；交互路径使用离线假命令验证，不代表真实 macOS 安装成功。未执行任何真实包安装、Docker 启动或模型推理。
