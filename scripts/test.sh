#!/bin/bash
# 仅运行项目自有测试；不安装依赖、不下载模型、不启动实际模型服务。
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
REMOTE=0
usage() {
  cat <<'HELP'
用法：bash scripts/test.sh [--remote]

默认：Shell 语法检查及工具包 Python 离线回归，需要 Python 3.10+。
--remote：另运行 Remote 启动器和 Node 回归，需要 Node.js 24+ 与 Git。
          仅使用临时文件、本地 Git 仓库及回环模拟服务，不访问外部仓库。
--help：显示帮助。

不自动安装缺失依赖；缺少依赖或未知参数返回 2，测试失败返回非零。
HELP
}
for arg in "$@"; do
  case "$arg" in
  --remote) REMOTE=1 ;;
  --help | -h)
    usage
    exit 0
    ;;
  *)
    printf '未知参数：%s\n' "$arg" >&2
    usage >&2
    exit 2
    ;;
  esac
done
command -v python3 >/dev/null 2>&1 || {
  printf '需要已安装的 Python 3.10+。\n' >&2
  exit 2
}
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  printf '需要 Python 3.10+。\n' >&2
  exit 2
}
if [ "$REMOTE" -eq 1 ]; then
  command -v node >/dev/null 2>&1 && command -v git >/dev/null 2>&1 || {
    printf 'Remote 测试需要已安装的 Node.js 24+ 和 Git。\n' >&2
    exit 2
  }
  node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 24 ? 0 : 1)' || {
    printf 'Remote 测试需要 Node.js 24+。\n' >&2
    exit 2
  }
fi
# 隔离用户配置、缓存及 Git 签名/钩子配置，清理仅限本次创建的临时目录。
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mac-ai-kit-tests.XXXXXX")"
trap 'rm -rf -- "$TEST_ROOT"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# macOS 的 /var 是指向 /private/var 的系统链接；测试夹具必须使用真实路径。
TEST_ROOT="$(CDPATH= cd -P -- "$TEST_ROOT" && pwd -P)"
mkdir -p "$TEST_ROOT/home" "$TEST_ROOT/tmp"
export HOME="$TEST_ROOT/home" TMPDIR="$TEST_ROOT/tmp"
export PYTHONDONTWRITEBYTECODE=1 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
cd "$ROOT"
printf '检查 Bash 语法…\n'
for script in start.sh scripts/*.sh remote/*.sh remote/src/*.sh; do
  bash -n "$script"
done
printf '核对 Remote 单文件发行版与源码一致性…\n'
python3 scripts/build_remote.py --check
printf '运行工具包 Python 离线回归…\n'
python3 -m unittest discover -s tests -q
if [ "$REMOTE" -eq 1 ]; then
  printf '运行 Remote 模拟启动器与本地回环回归…\n'
  python3 -m unittest discover -s remote/tests -p 'test_*.py' -q
  node --test remote/tests/*.test.cjs
else
  printf '未运行 Remote 测试；完整检查请添加 --remote。\n'
fi
printf '本次选择的检查全部通过。\n'
