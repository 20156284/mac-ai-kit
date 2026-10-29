#!/usr/bin/env bash
# 编写与预览可在任意系统安全进行；--apply 仅在原生 Apple Silicon macOS 上安装。
set -euo pipefail
. "$(cd -- "$(dirname -- "$0")" && pwd)/path_validation.sh"
ROOT="${HOME}/AI"
DETAILS=0
CHOOSE_ONLY=0
CONFIRM_ONLY=0
QUIET_PLAN=0
APPLY=0
YES=0
SELECT=""
TEMP=""
CURRENT=""
FAIL=0
MODELS_DIR=""
MOUNT=""
APP_PATH=""
APP_STAGE=""
usage() {
  cat <<'HELP'
用法：bash scripts/setup_ai_capabilities.sh [--select 组件] [--root 目录] [--models-dir 目录] [--dry-run | --apply] [--yes] [--details]
默认仅预览；加 --apply 安装。外部程序不覆盖；sd.cpp 可升级本工具包的受管版本。
可选组件：mlx-serve,llama.cpp,llama-swap,sd.cpp,audio.cpp,hermes（all 表示常用组件，不含可选 llama-swap）。
MLX-Serve 仅安装桌面 App；请手动打开、加载模型并开启 API。
已有 App 与其他工具保持原安装路径，不配置 MLX-Serve 命令行入口。
--models-dir 设置已有的模型目录；不会下载模型。
--yes 须配合 --apply 和 --select；--details 查看版本、来源和校验信息。
HELP
}
while (($#)); do
  case "$1" in
  --select | --root | --models-dir)
    (($# >= 2)) || {
      echo "缺少参数值： $1" >&2
      exit 2
    }
    case "$1" in --select) SELECT="$2" ;; --root) ROOT="$2" ;; --models-dir)
      trim_path_input "$2"
      MODELS_DIR="$TRIMMED_PATH"
      [[ -n "$MODELS_DIR" ]] || {
        echo "模型目录不能为空。" >&2
        exit 2
      }
      ;;
    esac
    shift 2
    ;;
  --quiet-plan)
    QUIET_PLAN=1
    shift
    ;;
  --confirm-only)
    CONFIRM_ONLY=1
    shift
    ;;
  --choose-only)
    CHOOSE_ONLY=1
    shift
    ;;
  --details)
    DETAILS=1
    shift
    ;;
  --apply)
    APPLY=1
    shift
    ;;
  --dry-run)
    APPLY=0
    shift
    ;;
  --yes)
    YES=1
    shift
    ;;
  -h | --help)
    usage
    exit 0
    ;;
  *)
    echo "未知选项： $1" >&2
    exit 2
    ;;
  esac
done
((YES == 0 || APPLY == 1)) || {
  echo "--yes 必须搭配 --apply" >&2
  exit 2
}
if [[ -n "$MODELS_DIR" ]]; then
  validate_model_root_shell "$MODELS_DIR" || exit $?
  [[ "$MODELS_DIR" = /* && "$MODELS_DIR" != / && "$MODELS_DIR" != *$'\n'* ]] || {
    echo "--models-dir 必须为不含换行符的绝对路径，且不能是根目录" >&2
    exit 2
  }
fi
[[ "$ROOT" = /* && "$ROOT" != / && "$ROOT" != *$'\n'* ]] || {
  echo 'ROOT 必须为不含换行符的绝对路径，且不能是根目录' >&2
  exit 2
}
selected() { [[ ",$SELECT," = *",$1,"* ]]; }
metadata() {
  case "$1" in
  mlx-serve)
    VERSION=v26.10.2
    CMD=mlx-serve
    EXT=dmg
    URL=https://github.com/ddalcu/mlx-serve/releases/download/v26.10.2/MLX-Serve.dmg
    SHA=2f49992cb5aca1cd91b59093d1c9f7ecf6166e842312600e1d0cd1c2574ed60a
    ;;
  llama.cpp)
    VERSION=b11669
    CMD=llama-server
    EXT=tar.gz
    URL=https://github.com/ggml-org/llama.cpp/releases/download/b11669/llama-b11669-bin-macos-arm64.tar.gz
    SHA=dbed55493d72bc1ba2babc8770d669c2289b5bc4ee9fb2a4dfc158d45b7d13fd
    ;;
  llama-swap)
    VERSION=v265
    CMD=llama-swap
    EXT=tar.gz
    URL=https://github.com/mostlygeek/llama-swap/releases/download/v265/llama-swap_265_darwin_arm64.tar.gz
    SHA=01d8d543198973dcfa52fd263e5696230ee1a77fb204f48c5d542761a67623b0
    ;;
  sd.cpp)
    VERSION=latest
    CMD=sd-server
    EXT=zip
    URL=https://api.github.com/repos/leejet/stable-diffusion.cpp/releases/latest
    SHA=官方发行摘要
    ;;
  audio.cpp)
    VERSION=v0.9.2-drama3-base-small
    CMD=audiocpp_server
    EXT=tar.gz
    URL=https://github.com/0xShug0/audio.cpp/releases/download/v0.9.2-drama3-base-small/audio-v0.9.2-drama3-base-small-bin-macos-arm64-metal.tar.gz
    SHA=1281998c1acd170a7d92139853fe6ce6ab920e211d2ad3359775d43ad9ba616d
    ;;
  hermes)
    VERSION=v0.22.2
    CMD=hermes
    EXT=sh
    COMMIT=d923a5eb487a17c0a68801bba9ef0f56c81040a8
    URL=https://raw.githubusercontent.com/NousResearch/hermes-agent/d923a5eb487a17c0a68801bba9ef0f56c81040a8/scripts/install.sh
    SHA=f4cdb0e20f1aca2c3871a76b962fd4db61510f549cf973759c753ac13349beb5
    ;;
  esac
}
# 只检查路径，不调用已有组件，不运行版本探测或启动服务。
component_state() {
  local candidate found=''
  metadata "$1"
  if [[ "$1" == mlx-serve ]]; then
    for candidate in "$HOME/Applications/MLX-Serve.app" /Applications/MLX-Serve.app; do
      if [[ -e "$candidate" || -L "$candidate" ]]; then
        found="$candidate"
        break
      fi
    done
    if [[ -n "$found" ]]; then printf 'App 已找到，保留'; else printf '未找到 App，按需安装'; fi
    return
  fi
  if [[ "$1" == sd.cpp ]]; then
    if [[ (-e "$ROOT/bin/$CMD" || -L "$ROOT/bin/$CMD") && ! -x "$ROOT/bin/$CMD" ]]; then
      printf '已有入口异常，保留不覆盖；安装官方最新版'
    else printf '官方最新版；受管版本可升级，外部程序保留'; fi
    return
  fi
  if command -v "$CMD" >/dev/null 2>&1 || [[ -x "$ROOT/bin/$CMD" ]]; then
    printf '已找到，保留（未运行核验）'
  elif [[ -e "$ROOT/bin/$CMD" || -L "$ROOT/bin/$CMD" ]]; then
    printf '已有入口异常，安装时停止，不覆盖'
  elif [[ "$1" == hermes && (-e "${HERMES_HOME:-$HOME/.hermes}" || -L "${HERMES_HOME:-$HOME/.hermes}" || -e "$HOME/.local/bin/hermes" || -L "$HOME/.local/bin/hermes") ]]; then
    printf '已有数据或入口，需检查 PATH，不覆盖'
  else printf '未找到，按需安装'; fi
}
normalize_selection() {
  local token id normalized='' input="$1" tokens=()
  [[ "$input" != all ]] || input='1 2 4 5 6'
  input="${input//,/ }"
  IFS=$' \t\n' read -r -a tokens <<<"$input"
  for token in "${tokens[@]}"; do
    case "$token" in
    1 | mlx-serve) id=mlx-serve ;; 2 | llama.cpp) id=llama.cpp ;;
    3 | llama-swap) id=llama-swap ;; 4 | sd.cpp) id=sd.cpp ;;
    5 | audio.cpp) id=audio.cpp ;; 6 | hermes) id=hermes ;;
    *) return 1 ;;
    esac
    case ",$normalized," in *",$id,"*) ;; *) normalized="${normalized:+$normalized,}$id" ;; esac
  done
  [[ -n "$normalized" ]] || return 1
  SELECT="$normalized"
}
choose_components() {
  local number=0 id label input
  echo '选择要安装的工具（外部程序保留；sd.cpp 受管旧版可升级）：' >&2
  for id in mlx-serve llama.cpp llama-swap sd.cpp audio.cpp hermes; do
    number=$((number + 1))
    case "$id" in
    mlx-serve) label='MLX-Serve：大模型 / 音乐 / 视频（桌面 App，手动开启 API）' ;;
    llama.cpp) label='llama.cpp：运行 GGUF 大模型（CLI / 本机 API）' ;;
    llama-swap) label='llama-swap：切换多个模型服务（可选）' ;;
    sd.cpp) label='sd.cpp：图像生成与模型切换（CLI / 本机 API）' ;;
    audio.cpp) label='audio.cpp：Qwen 转写 / YuE2 音乐（CLI + API）' ;;
    hermes) label='Hermes：个人 AI 助手（终端 CLI）' ;;
    esac
    printf '  %s %s\n    状态：%s\n' "$number" "$label" "$(component_state "$id")" >&2
  done
  while :; do
    printf '输入 all 选择常用组件（不含可选 llama-swap）；或输入编号，如 1,2,5。\n空白回车或 0 返回；h 查看帮助。下一步显示安装清单：' >&2
    IFS= read -r input || {
      SELECT=''
      return 0
    }
    case "$input" in '' | 0 | q)
      SELECT=''
      return 0
      ;;
    h | help | --help)
      usage >&2
      continue
      ;;
    esac
    if normalize_selection "$input"; then return 0; fi
    echo '选择无效，请输入 1–6 的编号、all 或 0。' >&2
  done
}
if [[ -z "$SELECT" ]]; then
  if ((APPLY || CHOOSE_ONLY || CONFIRM_ONLY)); then
    ((YES == 0)) || {
      echo '--yes 需要明确指定 --select' >&2
      exit 2
    }
    choose_components
    [[ -n "$SELECT" ]] || {
      echo '已取消。' >&2
      exit 0
    }
  else SELECT=all; fi
fi
normalize_selection "$SELECT" || {
  echo "无效组件选择：$SELECT" >&2
  exit 2
}
if ((CHOOSE_ONLY)); then
  printf '%s\n' "$SELECT"
  exit 0
fi
if ((QUIET_PLAN)) && ((APPLY == 0 || YES == 0)); then
  echo '--quiet-plan 仅用于已确认的明确安装选择' >&2
  exit 2
fi
if ((CONFIRM_ONLY)); then exec 3>&1 1>&2; fi
if ((QUIET_PLAN == 0)); then
  printf '%s\n' "$([[ $APPLY == 1 ]] && echo '将安装：' || echo '安装预览：')"
  if [[ -n "$MODELS_DIR" ]]; then printf '模型目录：%s\n' "$MODELS_DIR"; fi
  if selected mlx-serve && [[ -n "$MODELS_DIR" ]]; then
    echo '将更改 MLX-Serve 默认模型目录并备份旧设置；请先退出应用。'
    if ((DETAILS)); then printf '偏好项：com.dalcu.mlx-core/defaultModelDownloadPath；备份：%s/backups；仅支持已核验的 26.10.2 非商店版。\n' "$ROOT"; fi
  fi
  for id in mlx-serve llama.cpp llama-swap sd.cpp audio.cpp hermes; do
    selected "$id" || continue
    metadata "$id"
    if [[ "$id" == mlx-serve ]]; then
      printf '  MLX-Serve 桌面 App → ~/Applications/MLX-Serve.app（已有则保留）\n'
    elif [[ "$id" == sd.cpp ]]; then
      printf '  SD.cpp → %s/apps/sd.cpp（官方最新发行版）\n' "$ROOT"
      echo '    下载官方安装包；校验后更新，已有外部命令保留。'
    elif command -v "$CMD" >/dev/null 2>&1 || [[ -x "$ROOT/bin/$CMD" ]]; then
      printf '  %s：保留已有程序（版本未核验）\n' "$id"
    elif [[ "$id" == hermes ]]; then
      printf '  Hermes → ~/.hermes、~/.local/bin\n'
    else printf '  %s → %s/apps/%s-%s\n' "$id" "$ROOT" "$id" "$VERSION"; fi
    if ((DETAILS)); then
      printf '  %s %s -> %s\n  %s\n  SHA256 %s\n' "$id" "$VERSION" "$CMD" "$URL" "$SHA"
      if [[ "$id" != mlx-serve ]] && command -v "$CMD" >/dev/null 2>&1; then printf '  已有命令：%s\n' "$(command -v "$CMD")"; fi
    fi
  done
  if selected hermes; then
    echo 'Hermes 最后安装：会安装 Python、Node/工具链等依赖并构建界面，可能修改 shell PATH 配置文件。'
    if ((DETAILS)); then
      cat <<'NOTICE'
Hermes 使用固定版本的官方安装器，自行管理 Python 3.14、uv 和依赖。
参数：--commit <固定提交 SHA> --non-interactive --skip-browser --skip-computer-use
不使用 --include-desktop，不配置模型服务商、不安装网关。
禁用浏览器和计算机操作的选择由 Hermes 包管理器保留；不会覆盖已有 Hermes 目录。
NOTICE
    fi
  fi
fi
confirm_install() {
  printf '安装以上组件及已说明的依赖 / 设置更改？[y/N]（y 安装，回车取消）：' >&2
  IFS= read -r answer || return 1
  [[ "$answer" == y || "$answer" == Y || "$answer" == yes || "$answer" == YES ]]
}
if ((CONFIRM_ONLY)); then
  echo '若缺少 Python / uv，会先进入基础环境准备，单独确认一份基础工具计划；不会下载模型或启动服务。'
  if confirm_install; then printf '%s\n' "$SELECT" >&3; else echo '已取消。'; fi
  exit 0
fi
((APPLY)) || {
  echo
  echo '仅预览，未安装；从「安装运行工具 → 安装推理与生成引擎」选择安装。'
  exit 0
}
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || {
  echo '安装需要原生 arm64 macOS（不能使用 Rosetta）。' >&2
  exit 2
}
[[ "$(id -u)" != 0 ]] || {
  echo '请以普通用户运行，不要使用 sudo/root。' >&2
  exit 2
}
for tool in curl shasum tar; do command -v "$tool" >/dev/null || {
  echo "缺少 $tool；请先运行 setup_mac_environment.sh。" >&2
  exit 2
}; done
PYTHON=""
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(sys.version_info < (3,10))' 2>/dev/null; then
  PYTHON=$(command -v python3)
elif command -v uv >/dev/null 2>&1; then
  candidate=$(uv python find --no-python-downloads 3.12 2>/dev/null || true)
  if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3,10))'; then PYTHON="$candidate"; fi
fi
[[ -n "$PYTHON" ]] || {
  echo '未找到 Python 3.10+。请先运行 setup_mac_environment.sh；此处不会隐式下载 Python。' >&2
  exit 2
}
if selected mlx-serve; then
  for tool in hdiutil ditto defaults codesign pgrep; do command -v "$tool" >/dev/null || {
    echo "缺少 macOS 工具： $tool" >&2
    exit 2
  }; done
fi
if [[ -n "$MODELS_DIR" && ! -d "$MODELS_DIR" ]]; then
  echo '共享模型目录不存在。请先挂载外置磁盘或创建指定文件夹；不会回退到其他目录。' >&2
  exit 2
fi
if selected hermes; then
  command -v git >/dev/null || {
    echo 'Hermes 需要 git。' >&2
    exit 2
  }
  if ! command -v hermes >/dev/null 2>&1 && { [[ -e "${HERMES_HOME:-$HOME/.hermes}" || -L "${HERMES_HOME:-$HOME/.hermes}" ]] || [[ -e "$HOME/.local/bin/hermes" || -L "$HOME/.local/bin/hermes" ]]; }; then
    echo '发现已有 Hermes 数据或启动器，但不在 PATH 中。请检查并修复 PATH；本包装脚本不会修复或覆盖它。' >&2
    exit 2
  fi
fi
if ((YES == 0)); then
  [[ -t 0 ]] || {
    echo '确认需要交互终端；审阅计划后，可配合明确的组件选择使用 --yes。' >&2
    exit 2
  }
  confirm_install || {
    echo 已取消
    exit 0
  }
fi
TEMP=$(mktemp -d "${TMPDIR:-/tmp}/lean-ai-capabilities.XXXXXXXX")
cleanup() {
  code=$?
  if [[ -n "$MOUNT" ]]; then
    if ! hdiutil detach "$MOUNT" >/dev/null 2>&1; then
      echo "无法卸载 $MOUNT；保留临时目录，请手动推出磁盘映像。" >&2
      return
    fi
  fi
  [[ -z "$APP_STAGE" ]] || rm -rf "$APP_STAGE"
  [[ -z "$TEMP" ]] || rm -rf "$TEMP"
  if ((code != 0)); then printf '失败：%s；此前成功安装的组件将保留，不代表推理已可用。\n' "$CURRENT" >&2; fi
}
trap cleanup EXIT
validate_gui() {
  [[ -d "$1" && ! -L "$1" && -x "$1/Contents/MacOS/MLXCore" ]] || {
    echo "MLX-Serve 应用无效或不完整： $1" >&2
    return 1
  }
  "$PYTHON" - "$1/Contents/Info.plist" <<'PY'
import plistlib, sys
with open(sys.argv[1], 'rb') as f: info=plistlib.load(f)
if info.get('CFBundleIdentifier') != 'com.dalcu.mlx-core': raise SystemExit('图形应用包标识与预期不符')
PY
}
for id in mlx-serve llama.cpp llama-swap sd.cpp audio.cpp hermes; do
  selected "$id" || continue
  CURRENT="$id"
  metadata "$id"
  if [[ "$id" == mlx-serve ]]; then
    for existing in "$HOME/Applications/MLX-Serve.app" /Applications/MLX-Serve.app; do
      if [[ -L "$existing" ]]; then
        echo "已阻止：应用路径是符号链接： $existing" >&2
        exit 1
      fi
      if [[ -e "$existing" ]]; then
        validate_gui "$existing"
        APP_PATH="$existing"
        break
      fi
    done
    if [[ -n "$APP_PATH" ]]; then
      echo "已复用（版本未经核验）: $APP_PATH"
      continue
    fi
  elif [[ "$id" == sd.cpp ]]; then
    # 本次只解析一次 latest，随后下载与发布共用这一份发行计划。
    "$PYTHON" "$(dirname -- "$0")/sd_release.py" resolve --out "$TEMP/sd-release.json"
    "$PYTHON" "$(dirname -- "$0")/sd_release.py" install --root "$ROOT" --plan "$TEMP/sd-release.json"
    continue
  elif command -v "$CMD" >/dev/null 2>&1 || [[ -x "$ROOT/bin/$CMD" ]]; then
    echo "已复用（版本未经核验）: $id -> $(command -v "$CMD" || printf '%s' "$ROOT/bin/$CMD")"
    continue
  fi
  if [[ "$id" != mlx-serve && "$id" != sd.cpp && (-e "$ROOT/bin/$CMD" || -L "$ROOT/bin/$CMD") ]]; then
    echo "已阻止：$ROOT/bin/$CMD 已存在；请先加入 PATH 或检查它。" >&2
    exit 1
  fi
  if [[ "$id" == mlx-serve ]]; then
    minimum=26.2
    "$PYTHON" - "$(sw_vers -productVersion)" "$minimum" <<'PY'
import sys
v=lambda s: tuple((list(map(int,s.split('.')))+[0,0])[:3])
if v(sys.argv[1]) < v(sys.argv[2]):
 raise SystemExit('已阻止：按保守要求，所选构建需要 macOS '+sys.argv[2]+'；请检查上游要求或选择其他组件。')
PY
  fi
  archive="$TEMP/$id.$EXT"
  curl --fail --location --proto '=https' --tlsv1.2 --retry 3 --output "$archive" "$URL"
  actual=$(shasum -a 256 "$archive" | awk '{print $1}')
  [[ "$actual" == "$SHA" ]] || {
    echo "$id 的 SHA256 校验不匹配" >&2
    exit 1
  }
  if [[ "$id" == mlx-serve ]]; then
    MOUNT="$TEMP/mlx-dmg"
    mkdir -p "$MOUNT"
    # 不向许可提示自动输入答案；遇到意外交互或软件许可协议时安全停止。
    hdiutil attach "$archive" -readonly -nobrowse -noautoopen -mountpoint "$MOUNT" </dev/null
    bundle="$MOUNT/MLX-Serve.app"
    "$PYTHON" - "$bundle/Contents/Info.plist" <<'PY'
import plistlib, sys
with open(sys.argv[1], 'rb') as f: info=plistlib.load(f)
if info.get('CFBundleIdentifier') != 'com.dalcu.mlx-core' or info.get('CFBundleShortVersionString') != '26.10.2':
 raise SystemExit('MLX-Serve 应用包标识或版本不符；拒绝复制')
PY
    [[ -x "$bundle/Contents/MacOS/MLXCore" && ! -L "$bundle" ]] || {
      echo '图形应用包缺失或无效。' >&2
      exit 1
    }
    mkdir -p "$HOME/Applications"
    APP_PATH="$HOME/Applications/MLX-Serve.app"
    [[ ! -e "$APP_PATH" && ! -L "$APP_PATH" ]] || {
      echo '安装过程中目标应用已出现；未覆盖。' >&2
      exit 1
    }
    APP_STAGE=$(mktemp -d "$HOME/Applications/.lean-ai-mlx.XXXXXXXX")
    ditto "$bundle" "$APP_STAGE/MLX-Serve.app"
    validate_gui "$APP_STAGE/MLX-Serve.app"
    [[ ! -e "$APP_PATH" && ! -L "$APP_PATH" ]] || {
      echo '复制过程中目标应用已出现；未覆盖。' >&2
      exit 1
    }
    mv -n "$APP_STAGE/MLX-Serve.app" "$HOME/Applications/"
    [[ ! -e "$APP_STAGE/MLX-Serve.app" ]] || {
      echo "目标位置已出现应用；临时副本未发布。" >&2
      exit 1
    }
    rmdir "$APP_STAGE"
    APP_STAGE=""
    hdiutil detach "$MOUNT"
    MOUNT=""
    echo "已安装图形应用（未启动、未做运行测试）: $APP_PATH"
    continue
  fi
  if [[ "$id" == hermes ]]; then
    # 仅在本脚本通过明确的 --apply 确认后，执行固定版本的官方代码。
    env -u HERMES_REPO_URL -u HERMES_INSTALL_DIR bash "$archive" --commit "$COMMIT" --non-interactive --skip-browser --skip-computer-use
    launcher="$HOME/.local/bin/hermes"
    [[ -x "$launcher" ]] || {
      echo 'Hermes 安装器已结束，但未找到启动器。' >&2
      exit 1
    }
    echo "已安装（未配置、未做推理测试）: $launcher"
    continue
  fi
  mkdir -p "$ROOT/apps" "$ROOT/bin"
  target="$ROOT/apps/$id-$VERSION"
  [[ ! -e "$target" && ! -L "$target" ]] || {
    echo "已阻止：$target 已存在；请检查此前或未完成的安装，未覆盖。" >&2
    exit 1
  }
  stage="$TEMP/unpack-$id"
  mkdir -p "$stage"
  "$PYTHON" - "$archive" "$stage" <<'PY'
import os, pathlib, stat, sys, tarfile, zipfile
src, dst=sys.argv[1:]; root=pathlib.Path(dst).resolve()
def safe(name):
 raw=pathlib.PurePosixPath(name)
 if raw.is_absolute() or '..' in raw.parts: raise ValueError('不安全的归档路径：'+name)
 p=(root/name).resolve()
 if p != root and root not in p.parents: raise ValueError('不安全的归档路径：'+name)
if src.endswith('.zip'):
 with zipfile.ZipFile(src) as z:
  for m in z.infolist():
   safe(m.filename)
   if stat.S_ISLNK(m.external_attr >> 16): raise ValueError('拒绝 ZIP 中的符号链接')
  z.extractall(dst)
  for m in z.infolist():
   mode=(m.external_attr >> 16) & 0o777
   if mode: os.chmod(root/m.filename,mode)
else:
 with tarfile.open(src) as t:
  for m in t.getmembers():
   safe(m.name)
   if m.isdev() or m.isfifo(): raise ValueError('拒绝归档中的特殊文件条目')
   if m.issym():
    safe(m.linkname)
    safe(str(pathlib.PurePosixPath(m.name).parent/m.linkname))
   if m.islnk(): safe(m.linkname)
  t.extractall(dst)
PY
  executable=$(find "$stage" -type f -name "$CMD" -print -quit)
  [[ -n "$executable" && -x "$executable" ]] || {
    echo "已阻止：归档中缺少可执行文件 $CMD；不标记为已安装。" >&2
    exit 1
  }
  relative="${executable#"$stage"/}"

  mv "$stage" "$target"
  # 包装脚本指向实际二进制程序；所有随包动态库与资源保持原始目录布局。
  {
    printf '#!/usr/bin/env bash\nexec '
    printf '%q ' "$target/$relative"
    printf '"$@"\n'
  } >"$ROOT/bin/$CMD"
  chmod 755 "$ROOT/bin/$CMD"
  printf '%s\n' "$SHA  $URL" >"$target/.lean-ai-release"
  echo "已安装（未做运行测试）: $id -> $ROOT/bin/$CMD"
done
if selected mlx-serve && [[ -n "$MODELS_DIR" ]]; then
  CURRENT="MLX-Serve 共享模型设置"
  validate_gui "$APP_PATH"
  [[ ! -e "$APP_PATH/Contents/_MASReceipt" ]] || {
    echo '检测到 Mac App Store 或沙盒应用；请改用图形界面选择文件夹。' >&2
    exit 1
  }
  if ! codesign -d --entitlements :- "$APP_PATH" >"$TEMP/mlx-entitlements.plist" 2>"$TEMP/codesign.log"; then
    echo '无法核验应用权限声明；请在图形界面中手动选择默认模型文件夹。偏好设置未改变。' >&2
    exit 1
  fi
  "$PYTHON" - "$TEMP/mlx-entitlements.plist" <<'PY'
import pathlib, plistlib, sys
raw=pathlib.Path(sys.argv[1]).read_bytes().strip()
if raw and plistlib.loads(raw).get('com.apple.security.app-sandbox', False):
 raise SystemExit('检测到沙盒图形应用；请在图形界面选择模型文件夹以授予访问权限')
PY
  running=0
  pgrep -x MLXCore >/dev/null 2>&1 || running=$?
  if [[ "$running" == 0 ]]; then
    echo '更改文件夹前请退出 MLX-Serve。偏好设置未改变；退出后请重新运行。' >&2
    exit 1
  fi
  [[ "$running" == 1 ]] || {
    echo '无法确认 MLX-Serve 是否运行；偏好设置未改变。' >&2
    exit 1
  }
  # 此键与偏好域已经固定版本的官方源码核验，并非工具包自定义配置。
  "$PYTHON" - "$APP_PATH/Contents/Info.plist" <<'PY'
import plistlib, sys
with open(sys.argv[1], 'rb') as f: info=plistlib.load(f)
if info.get('CFBundleShortVersionString') != '26.10.2':
 raise SystemExit('App 已保留，但共享目录偏好未改变：现有图形应用版本与已核验的 26.10.2 不同；请手动进入设置 > 模型文件夹 > 默认文件夹（Settings > Model Folders > Default folder）选择')
PY
  [[ -d "$MODELS_DIR" && -r "$MODELS_DIR" && -w "$MODELS_DIR" ]] || {
    echo '共享模型目录不可用或缺少读写权限；拒绝更新偏好设置。' >&2
    exit 1
  }
  mkdir -p "$ROOT/backups"
  backup=$(mktemp "$ROOT/backups/mlx-defaultModelDownloadPath.XXXXXXXX")
  if prior=$(defaults read com.dalcu.mlx-core defaultModelDownloadPath 2>/dev/null); then printf 'PRESENT\n%s\n' "$prior" >"$backup"; else printf 'ABSENT\n' >"$backup"; fi
  echo "旧偏好设置已记录： $backup"
  defaults write com.dalcu.mlx-core defaultModelDownloadPath -string "$MODELS_DIR"
  [[ "$(defaults read com.dalcu.mlx-core defaultModelDownloadPath)" == "$MODELS_DIR" ]] || {
    echo '偏好设置写入后的回读核验失败。' >&2
    exit 1
  }
  echo "已配置官方图形界面的默认模型文件夹： $MODELS_DIR"
  echo '每次启动前请挂载该磁盘。目录不可用时，上游应用可能静默回退到 ~/.mlx-serve/models；下载前请检查设置 > 模型文件夹（Settings > Model Folders）。'
fi
# 这里只汇报实际存在的所选命令入口；不暗示所有软件都安装在 bin 中。
entry_names=''
for id in llama.cpp llama-swap sd.cpp audio.cpp hermes; do
  selected "$id" || continue
  metadata "$id"
  if [[ -f "$ROOT/bin/$CMD" && -x "$ROOT/bin/$CMD" ]]; then
    entry_names="${entry_names:+$entry_names、}$CMD"
  fi
done
if [[ -n "$entry_names" ]]; then
  printf '\n命令入口：%s/bin（%s）\n' "$ROOT" "$entry_names"
  echo '这里只放启动脚本，调用实际安装的程序；不是完整运行环境。'
  case ":$PATH:" in
  *":$ROOT/bin:"*) : ;;
  *) echo '菜单内无需设置 PATH；在其他终端调用时可使用入口的完整路径。' ;;
  esac
else
  echo '所选工具保留各自安装路径，本次没有可列出的工具包命令入口。'
fi
echo '安装结束，尚未下载模型或启动服务。'
echo '启动项：本次没有创建登录自动启动项；也没有探测已有服务运行状态。'
if selected llama-swap; then
  echo 'llama-swap：程序入口已准备；配置和运行状态尚未核验。'
fi
if selected mlx-serve; then echo 'MLX-Serve：请手动打开桌面 App，选择模型并开启 API。'; fi
if selected llama-swap; then echo 'llama-swap：可在主菜单的可选 llama-swap 管理中检查配置与状态。'; fi
