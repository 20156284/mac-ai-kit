#!/bin/bash
# Apple Silicon macOS 基础环境准备脚本。默认只读预览；兼容 Bash 3.2。
# 不要使用 sudo 运行本脚本；设计上不提供无人值守或 --yes 模式。
set -o pipefail
MODE=preview
DETAILS=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run|--preview) MODE=preview ;;
    --install) MODE=install ;;
    --details) DETAILS=1 ;;
    -h|--help) printf '用法： bash %s [--dry-run | --install] [--details]\n默认仅检查；--install 一次确认基础工具安装计划；--details 查看路径、版本和详细计划。\n' "$0"; exit 0 ;;
    *) printf '未知参数： %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done
NODE_MAJOR=24
FAILURES=0
SKIPPED=0
TEMP_DIR=
cleanup() { [ -z "$TEMP_DIR" ] || rm -rf -- "$TEMP_DIR"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
say() { printf '%s\n' "$*"; }
fail() { say "[未完成] $*" >&2; FAILURES=$((FAILURES + 1)); }
confirm() {
  local answer
  printf '\n%s [y/N]（y 执行，回车跳过） ' "$*"
  IFS= read -r answer || return 1
  case "$answer" in y|Y|yes|YES) return 0 ;; *) SKIPPED=$((SKIPPED + 1)); return 1 ;; esac
}
has() { command -v "$1" >/dev/null 2>&1; }
# 不对用户输入或用户配置执行 eval；仅经同意后追加官方初始化配置。
if [ -x /opt/homebrew/bin/brew ]; then
  BREW=/opt/homebrew/bin/brew
else
  BREW=$(command -v brew 2>/dev/null || true)
fi
report_tool() {
  local tool="$1" flag="$2" path
  path=$(command -v "$tool" 2>/dev/null || true)
  if [ -n "$path" ]; then
    if [ "$DETAILS" -eq 1 ]; then
      say "[$tool] $path"
      "$tool" "$flag" 2>/dev/null | head -n 1
    else say "[$tool] 已找到"; fi
  else say "[$tool] 未找到"; fi
}
report() {
  say ''; say '环境检查：'
  for tool in brew macmon uv python3 fnm node npm docker; do report_tool "$tool" --version; done
  report_tool ffmpeg -version
  local app
  for app in /Applications/Docker.app "$HOME/Applications/Docker.app"; do
    if [ -d "$app" ]; then
      if [ "$DETAILS" -eq 1 ]; then say "[Docker Desktop] $app"; else say '[Docker Desktop] 已找到'; fi
      if [ "$DETAILS" -eq 1 ] && [ -x /usr/libexec/PlistBuddy ]; then /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$app/Contents/Info.plist" 2>/dev/null || true; fi
    fi
  done
}
say '准备 Mac 基础工具：Homebrew、macmon、FFmpeg、uv、fnm/Node 24、Docker Desktop。'
if [ "$MODE" = preview ]; then
  if [ "$DETAILS" -eq 1 ]; then
    say '只读预览，不下载、不安装、不改配置。'
    say '缺少的基础工具合并为一次确认；Node 仅使用 fnm，发现其他管理器则暂停。'
    say '缺少 Python 3.10+ 时，可选通过 uv 准备 Python 3.12，不替换系统 Python。'
    say '配置修改包含在安装计划：Homebrew → ~/.zprofile，fnm → ~/.zshrc；只追加缺少的初始化。'
    say '不主动升级现有工具；安装新包可能更新必要依赖。'
  fi
  report
  say ''; say '从「安装运行工具 → 准备基础工具」补齐缺少的依赖。'
  [ "$DETAILS" -eq 0 ] || say '高级命令：bash scripts/setup_mac_environment.sh --install'
  exit 0
fi
say '先查看基础工具计划，再一次确认；Docker 为独立可选项。'
[ "$(uname -s)" = Darwin ] || { say '仅支持 macOS；本次没有安装。' >&2; exit 2; }
[ "$(uname -m)" = arm64 ] || { say '仅支持原生 Apple Silicon 终端。若在 Rosetta 下，请关闭 Rosetta 后重试。' >&2; exit 2; }
[ "$(id -u)" != 0 ] || { say '请以普通用户运行，不要 sudo 整个脚本。' >&2; exit 2; }
[ -t 0 ] && [ -t 1 ] || { say '--install 需要交互式终端，不能用管道自动回答。' >&2; exit 2; }
# 固定格式串与分离参数避免变量紧邻中文括号导致旧 Bash 显示异常。
# 不覆盖用户 locale；printf 直接输出文件中的 UTF-8 文本。
say ''; say '基础工具安装计划（已有工具跳过，不主动升级）：'
plan_tool() {
  local name="$1" purpose="$2" state='缺少，将安装'
  has "$name" && state='已找到，保留'
  printf '  %-8s | %s | %s\n' "$name" "$purpose" "$state"
}
plan_tool brew '软件包管理；缺少时下载并运行官方安装器'
plan_tool macmon '查看 Apple Silicon 系统负载'
plan_tool ffmpeg '音视频转码与处理'
plan_tool uv 'Python 环境及依赖管理'
plan_tool fnm 'Node 版本管理；检测到其他管理器时不修改'
plan_tool node 'Node 24 LTS；已有版本时保留；首次安装可建立 default 别名'
say '  Python   | 保留 Python 3.10+；缺少时用 uv 安装托管 3.12，不替换系统 Python'
say '配置：仅向 ~/.zprofile 追加缺少的 Homebrew 初始化，向 ~/.zshrc 追加缺少的 fnm 初始化。'
if [ "$DETAILS" -eq 1 ]; then
  say 'Homebrew 配置：eval "$(/opt/homebrew/bin/brew shellenv)"'
  say 'fnm 配置：eval "$(fnm env --shell zsh)"；未来终端使用 fnm 默认 Node。'
fi
say '保留现有文件和工具路径，不覆盖配置、不迁移旧管理器；安装新包可能更新必要依赖。'
say 'Homebrew 官方说明：https://docs.brew.sh/Installation'
say '若需要安装 Homebrew，官方安装器可能要求回车和管理员密码；这些系统授权仍由你本人处理。'
say '本计划不包括 Docker，不启动任何 AI 服务，也不创建登录启动项。'
confirm '执行以上基础工具安装和配置计划？' || { say '已取消基础环境准备。'; exit 0; }
if ! xcode-select -p >/dev/null 2>&1 || ! xcrun --find clang >/dev/null 2>&1; then
  say '缺少 Xcode Command Line Tools，需要在 Apple 系统界面完成安装。'
  if confirm '现在打开 Apple 安装界面？（条款和系统授权由你本人处理）'; then
    xcode-select --install || { say '系统未能打开安装界面，请检查系统提示后从菜单重试。'; exit 1; }
    printf '完成系统安装后按回车继续，或关闭本窗口取消：'
    IFS= read -r _ || exit 1
    if ! xcode-select -p >/dev/null 2>&1 || ! xcrun --find clang >/dev/null 2>&1; then
      say 'Command Line Tools 尚未就绪；本次停止，请完成系统安装后从菜单重试。'; exit 1
    fi
  else say '已取消基础环境准备。'; exit 1; fi
fi
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 HOMEBREW_NO_ANALYTICS=1
# 这些变量仅影响当前进程，不改变持久化用户设置。
if [ -z "$BREW" ]; then
  say 'Homebrew 官方说明：https://docs.brew.sh/Installation'
  say '官方安装器会创建 /opt/homebrew，可能要求管理员密码；请仅在本地终端输入。'
  TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/lean-ai-env.XXXXXX") || exit 1
  if /usr/bin/curl --fail --show-error --location --proto '=https' --tlsv1.2 --connect-timeout 20 --max-time 180 https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$TEMP_DIR/homebrew-install.sh"; then
    /bin/bash "$TEMP_DIR/homebrew-install.sh" || fail 'Homebrew 安装器失败；查看上方错误，修复后重跑。'
  else fail '下载 Homebrew 安装器失败；检查网络/代理，不要关闭 TLS 校验。'; fi
  [ ! -x /opt/homebrew/bin/brew ] || BREW=/opt/homebrew/bin/brew
fi
if [ -n "$BREW" ]; then
  PREFIX=$("$BREW" --prefix 2>/dev/null)
  if [ "$PREFIX" != /opt/homebrew ]; then
    fail "检测到非标准 Apple Silicon Homebrew：${BREW}（${PREFIX}）。为避免混装，本次不使用它。"
    BREW=
  else export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"; fi
fi
install_formula() {
  local name="$1"
  if has "$name"; then say "[保留] $name"; return; fi
  if [ -z "$BREW" ]; then fail "$name 缺失，Homebrew 不可用；先完成 brew 安装后重跑。"; return; fi
  if "$BREW" list --formula "$name" >/dev/null 2>&1; then
    fail "$name 已由 brew 安装但 PATH 不可用；请检查 brew info ${name}，不强制 relink。"; return
  fi
  printf '[安装] %s (brew install %s，含必要依赖)\n' "$name" "$name"
  {
    "$BREW" install "$name" || { fail "$name 安装失败；检查 brew doctor / brew info $name 后重跑。"; return; }
    has "$name" || fail "$name 安装结束但命令未找到；检查 PATH。"
  }
}
for formula in macmon ffmpeg uv; do install_formula "$formula"; done
# 环境准备和下载脚本需要较新版 Python；不替换 Apple 系统 Python。
for prerequisite in curl tar shasum git; do
  has "$prerequisite" || fail "缺少基础工具 ${prerequisite}；请检查 macOS / Xcode CLT 和 PATH。"
done
if has python3 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  say "[保留] Python"; if [ "$DETAILS" -eq 1 ]; then report_tool python3 --version; fi
elif has uv; then
  PYTHON_READY=$(uv python find --no-python-downloads 3.12 2>/dev/null || true)
  if [ -z "$PYTHON_READY" ]; then
    # 已在基础计划中说明并获得确认。
    {
      uv python install --no-bin 3.12 || fail 'uv 托管 Python 3.12 安装失败；检查网络后重跑。'
      PYTHON_READY=$(uv python find --no-python-downloads 3.12 2>/dev/null || true)
      [ -n "$PYTHON_READY" ] || fail '未找到可用托管 Python 3.12；后续能力安装器暂不可执行。'
    }
  fi
  [ -z "$PYTHON_READY" ] || say "[Python 路径] ${PYTHON_READY}（可显式用此路径运行后续 Python 脚本）"
else
  fail 'Python 3.10+ 与 uv 均不可用；先安装 uv，再重跑以准备 Python。'
fi

# 本工具包面向新 Mac 的 zsh；不加载任意用户配置并执行。
RC="$HOME/.zshrc"
append_block() {
  local marker="$1" body="$2" legacy_marker
  # 识别旧版初始化标记，避免升级后重复追加配置。
  legacy_marker="${marker/mac-ai-kit/lean-ai-kit}"
  if [ -L "$RC" ]; then fail "$RC 是符号链接，未修改；请手动添加初始化配置。"; return; fi
  if [ -f "$RC" ] && { grep -Fq "$marker" "$RC" || grep -Fq "$legacy_marker" "$RC"; }; then say "[保留] $RC 已包含 $marker"; return; fi
  say "追加到 ${RC}（不覆盖现有内容）："; printf '%s\n' "$body"
  # 追加操作已包含在确认的计划中；不再次询问。
  if printf '\n# %s\n%s\n' "$marker" "$body" >> "$RC"; then
    printf '[已配置] %s: %s\n' "$RC" "$marker"
  else fail "不能写入 ${RC}；请手动配置。"; fi
}
if [ -n "$BREW" ]; then
  RC="$HOME/.zprofile"
  if [ -f "$RC" ] && grep -Eq 'brew.*shellenv' "$RC"; then
    say "[保留] $RC 已有 Homebrew shellenv 初始化；不重复添加。"
  else
    append_block 'mac-ai-kit: homebrew shellenv' 'eval "$(/opt/homebrew/bin/brew shellenv)"'
  fi
  RC="$HOME/.zshrc"
fi
# 意外发现旧管理器视为冲突，不代表授权迁移或删除。
EXISTING_MANAGER=
for manager in nvm volta asdf mise; do
  if has "$manager"; then EXISTING_MANAGER="$manager"; break; fi
done
LEGACY_NVM_HOME=${NVM_DIR:-${XDG_CONFIG_HOME:+$XDG_CONFIG_HOME/nvm}}
LEGACY_NVM_HOME=${LEGACY_NVM_HOME:-$HOME/.nvm}
if [ -e "$LEGACY_NVM_HOME/nvm.sh" ] || [ -L "$LEGACY_NVM_HOME/nvm.sh" ]; then EXISTING_MANAGER=nvm; fi
for config in "$HOME/.zshrc" "$HOME/.zprofile"; do
  if [ -f "$config" ] && grep -Eq 'nvm\.sh|NVM_DIR|volta|asdf|mise activate' "$config"; then
    EXISTING_MANAGER='其他管理器的 shell 配置'
  fi
done
if [ -n "$EXISTING_MANAGER" ]; then
  fail "意外检测到 ${EXISTING_MANAGER}；暂停 Node/fnm 安装与初始化，请人工检查。本脚本不迁移、不卸载、不删除配置。"
else
    install_formula fnm
    if has fnm; then
      if has node; then
        say "[保留] Node"; if [ "$DETAILS" -eq 1 ]; then report_tool node --version; fi
      elif fnm list 2>/dev/null | grep -Eq 'v[0-9]+\.'; then
        say '[保留] fnm 已有 Node 版本；请执行 fnm list 并手动选择，不重复安装。'
      else
        printf '[安装] Node %s LTS (fnm)\n' "$NODE_MAJOR"
        fnm install "$NODE_MAJOR" || fail "Node 安装失败；检查网络后重试 fnm install ${NODE_MAJOR}。"
      fi
      # 此启动片段仅执行已安装 fnm 程序生成的初始化内容，
      # 绝不直接执行用户提供的命令或下载的字符串。
      if [ -n "$RC" ] && [ -f "$RC" ] && grep -Fq 'fnm env' "$RC"; then
        say "[保留] $RC 已有 fnm 初始化；不重复添加。"
      elif [ -n "$RC" ]; then
        FNM_BLOCK='eval "$(fnm env --shell zsh)"'
        say 'fnm 初始化会在未来终端中选择其默认 Node；本终端不执行用户 shell 配置。'
        append_block 'mac-ai-kit: fnm' "$FNM_BLOCK"
      fi
    fi
  fi
if [ -d /Applications/Docker.app ] || [ -d "$HOME/Applications/Docker.app" ]; then
  say '[保留] Docker Desktop 应用已存在；不升级、不启动。'
elif [ -z "$BREW" ]; then fail '无法安装 Docker Desktop：Homebrew 不可用。'
elif "$BREW" list --cask docker-desktop >/dev/null 2>&1 || "$BREW" list --cask docker >/dev/null 2>&1; then
  say '[保留] brew 已登记 Docker Desktop；请用 brew info --cask docker-desktop 核对位置。'
else
  say 'Docker Desktop 是图形应用，不只是 docker CLI。cask 安装可能创建 CLI 链接并请求本机管理员权限。'
  say '条款：https://www.docker.com/legal/docker-subscription-service-agreement/'
  say '个人/教育/非商业开源及符合条件的小企业可免费使用；较大企业商业使用需付费订阅。'
  say '本脚本不启动 Docker、不登录、不接受许可、不添加特权服务或自动启动设置。'
  if confirm '仅安装 Docker Desktop 应用及 cask 标准文件（brew install --cask docker-desktop）？'; then
    "$BREW" install --cask docker-desktop || fail 'Docker Desktop 安装失败；检查 brew info --cask docker-desktop。已有 CLI/链接冲突时不要强制覆盖。'
  fi
fi
report
say '配置结果：请查看上方 [保留] / 追加记录；没有创建任何登录启动项。'
say '服务状态：本次未启动 Docker 或 AI 服务，也未探测已有服务运行状态。'
say 'llama-swap 的配置、状态检查和启动请进入主菜单 6「服务管理」；安装不等于服务已就绪。'
say '若刚追加 shell 初始化，请打开新终端；请按需 fnm use 24。'
say 'Docker 请自行从 Applications 打开、阅读条款并选择权限；未启动时 docker info 失败是正常现象。'
say "结束：失败/需处理 $FAILURES 项；拒绝/跳过确认 $SKIPPED 项。已成功的安装不会自动回滚。"
[ "$FAILURES" -eq 0 ] || exit 1
exit 0
