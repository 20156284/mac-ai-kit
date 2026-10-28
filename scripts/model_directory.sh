#!/bin/bash
# Bash 3.2；设置只保存为单行数据，绝不 source / eval 用户配置。
model_directory_config() {
  MODEL_DIRECTORY_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/mac-ai-kit/model-directory"
}
load_model_directory() {
  model_directory_config
  MODEL_DIR="$HOME/AI/models"
  DIRECTORY_CHOSEN=0
  [ -e "$MODEL_DIRECTORY_FILE" ] || [ -L "$MODEL_DIRECTORY_FILE" ] || return 0
  if [ ! -f "$MODEL_DIRECTORY_FILE" ] || [ -L "$MODEL_DIRECTORY_FILE" ]; then
    printf '模型目录设置不是普通文件：%s；请修正后重试，不会退回默认目录。\n' "$MODEL_DIRECTORY_FILE" >&2
    return 2
  fi
  local saved
  saved="$(cat -- "$MODEL_DIRECTORY_FILE")" || return 2
  validate_model_root_shell "$saved" || return 2
  case "$VALIDATED_MODEL_ROOT" in /*) ;; *) printf '已保存的模型目录必须是绝对路径，不会退回默认目录。\n' >&2; return 2 ;; esac
  MODEL_DIR="$VALIDATED_MODEL_ROOT"
  DIRECTORY_CHOSEN=1
}
save_model_directory() {
  validate_model_root_shell "$1" || return $?
  local value="$VALIDATED_MODEL_ROOT" folder temporary
  model_directory_config
  folder="${MODEL_DIRECTORY_FILE%/*}"
  # 不执行配置内容，原子替换；失败时保留原设置。
  (umask 077; mkdir -p -- "$folder") || return $?
  temporary="$(umask 077; mktemp "$folder/.model-directory.XXXXXX")" || return $?
  if ! printf '%s\n' "$value" > "$temporary" || ! mv -f -- "$temporary" "$MODEL_DIRECTORY_FILE"; then
    rm -f -- "$temporary"
    printf '无法保存模型目录设置：%s\n' "$MODEL_DIRECTORY_FILE" >&2
    return 1
  fi
}
model_volume_ready() {
  local value="$1" volume rest mount_command
  case "$value" in /Volumes/*)
    rest="${value#/Volumes/}"; volume="/Volumes/${rest%%/*}"
    mount_command=/sbin/mount
    [ -x "$mount_command" ] || mount_command=/bin/mount
    if [ ! -d "$volume" ] || ! "$mount_command" | grep -F -- " on $volume (" >/dev/null; then
      printf '外置磁盘未挂载：%s。请连接并挂载后重试；保留模型目录 %s，不会改用默认目录或创建假挂载目录。\n' "$volume" "$value" >&2
      return 1
    fi ;;
  esac
}

# argparse-compatible last --models-dir wins, without changing the saved choice.
model_directory_for_args() {
  EFFECTIVE_MODEL_DIR="$MODEL_DIR"
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --models-dir) [ "$#" -ge 2 ] || return 2; EFFECTIVE_MODEL_DIR="$2"; shift ;;
      --models-dir=*) EFFECTIVE_MODEL_DIR="${1#*=}" ;;
    esac
    shift
  done
  trim_path_input "$EFFECTIVE_MODEL_DIR"
  EFFECTIVE_MODEL_DIR="$TRIMMED_PATH"
  case "$EFFECTIVE_MODEL_DIR" in '~') EFFECTIVE_MODEL_DIR="$HOME" ;; '~/'*) EFFECTIVE_MODEL_DIR="$HOME/${EFFECTIVE_MODEL_DIR#\~/}" ;; esac
  validate_model_root_shell "$EFFECTIVE_MODEL_DIR" || return $?
  model_volume_ready "$EFFECTIVE_MODEL_DIR"
}
