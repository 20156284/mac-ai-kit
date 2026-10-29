#!/bin/bash
# 采用 MIT 许可证。经明确确认后，安装用户级全局命令；依赖仍由 uv 隔离。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREVIEW=0
DETAILS=0
usage() {
  cat <<'HELP'
用法：bash scripts/bootstrap_download_env.sh [--dry-run] [--details]
安装模型下载工具，执行前会询问确认。
--dry-run  仅预览，不安装。
--details  查看版本、安装位置和技术说明。
HELP
}
while (($#)); do
  case "$1" in
  --dry-run) PREVIEW=1 ;;
  --details) DETAILS=1 ;;
  -h | --help)
    usage
    exit 0
    ;;
  *)
    printf '未知参数：%s\n' "$1" >&2
    exit 2
    ;;
  esac
  shift
done
if ((DETAILS)); then
  printf '%s\n' '下载工具：modelscope-hub 0.4.0；来源：官方 PyPI。'
  printf '%s\n' '通过 uv tool 安装，依赖独立隔离；不改系统 Python、PATH 或旧 .venv-download。'
  printf '%s\n' '命令：uv tool install --python <已有 Python 3.10+ 的路径> --no-python-downloads --default-index https://pypi.org/simple modelscope-hub==0.4.0'
  printf '%s\n' '提供 ms-hub、modelscope-hub、ms、modelscope 四个命令；不覆盖已有命令。'
fi
((PREVIEW == 0)) || {
  printf '%s\n' '将安装模型下载工具。本次仅预览，未安装。'
  exit 0
}
[[ "$(uname -s)" == Darwin ]] || {
  printf '%s\n' '仅支持 macOS。未安装任何软件。' >&2
  exit 1
}
[[ "$(id -u)" != 0 ]] || {
  printf '%s\n' '请以普通用户运行，不要使用 sudo/root。' >&2
  exit 1
}
command -v uv >/dev/null 2>&1 || {
  printf '%s\n' '未找到 uv；请先运行基础环境安装脚本，或自行安装官方 uv。' >&2
  exit 1
}
BIN_DIR=$(uv tool dir --bin)
TOOL_DIR=$(uv tool dir)
[[ "$BIN_DIR" == /* && "$TOOL_DIR" == /* ]] || {
  printf '%s\n' 'uv 工具目录不是绝对路径；请检查 uv 配置。' >&2
  exit 1
}
# 不运行来源未知的已有命令；只报告位置，避免隐式升级或覆盖。
for name in ms-hub modelscope-hub ms modelscope; do
  existing=$(command -v "$name" || true)
  if [[ -n "$existing" || -e "$BIN_DIR/$name" || -L "$BIN_DIR/$name" ]]; then
    printf '发现已有命令：%s\n' "${existing:-$BIN_DIR/$name}"
    printf '%s\n' '为避免混装或覆盖，本次未安装。请先人工核对来源和版本；已有 ms-hub 可通过下载器的 --cli 指定。'
    exit 1
  fi
done
if [[ -e "$TOOL_DIR/modelscope-hub" || -L "$TOOL_DIR/modelscope-hub" ]]; then
  printf '%s\n' '已存在 uv 的 modelscope-hub 工具环境；本次不升级或修复。请先运行 uv tool list 检查。' >&2
  exit 1
fi
PYTHON=$(uv python find --no-python-downloads '>=3.10' 2>/dev/null || true)
[[ -n "$PYTHON" && -x "$PYTHON" ]] || {
  printf '%s\n' '未找到本地 Python 3.10+；请先准备 Python，再重跑。此处不会自动下载 Python。' >&2
  exit 1
}
if ((DETAILS)); then printf 'Python：%s\n工具安装目录：%s\n命令目录：%s\n' "$PYTHON" "$TOOL_DIR" "$BIN_DIR"; fi
if ((DETAILS)) && [[ -e "$ROOT/.venv-download" || -L "$ROOT/.venv-download" ]]; then
  printf '%s\n' '检测到旧 .venv-download，将完整保留，不迁移、不删除；新工具使用独立的 uv 环境。'
fi
printf '将安装模型下载工具，是否继续？[y/N] '
read -r answer || exit 0
case "$answer" in y | Y | yes | YES | 是) ;; *)
  printf '%s\n' '已取消，未安装。'
  exit 0
  ;;
esac
if ((DETAILS)); then
  uv tool install --python "$PYTHON" --no-python-downloads --default-index https://pypi.org/simple 'modelscope-hub==0.4.0'
else
  uv tool install --python "$PYTHON" --no-python-downloads --default-index https://pypi.org/simple 'modelscope-hub==0.4.0' --quiet
fi
[[ -x "$BIN_DIR/ms-hub" ]] || {
  printf '%s\n' 'uv 已结束，但未找到可执行的 ms-hub；请运行 uv tool list 检查，不能视为安装完成。' >&2
  exit 1
}
printf '%s\n' '模型下载工具已安装。现在可以选择模型和下载位置。'
if ((DETAILS)); then
  printf '下载命令：%s/ms-hub\n' "$BIN_DIR"
  if ! command -v ms-hub >/dev/null 2>&1; then
    printf '%s\n' '命令未在 PATH 中，工具包会自动查找；也可使用 --cli 指定上述路径。'
    printf '%s\n' '若需在终端直接调用，可自行运行 uv tool update-shell 后重开终端。此命令会改 shell 配置，本脚本未执行。'
  fi
  printf '%s\n' '尚未下载模型、配置凭据或启动推理服务。'
fi
