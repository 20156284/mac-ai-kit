#!/bin/bash
# 兼容 macOS Bash 3.2；使用 ANSI、Bash 内建命令和系统自带的 stty 尺寸查询。
# read 的单键模式会自行恢复终端，不修改 stty、不隐藏光标。
ui_is_visual() {
  [ -t 0 ] && [ -t 1 ] && [ "${TERM:-dumb}" != dumb ] && [ -n "${TERM:-}" ] && [ -z "${NO_COLOR+x}" ]
}
# 单独 Esc 需要短暂等待，以区别方向键的转义序列；完整消费后再返回。
ui_read_key() {
  local key tail final sequence escape_timeout=1
  [ "${BASH_VERSINFO[0]}" -lt 4 ] || escape_timeout=0.2
  UI_KEY=''
  IFS= read -rsn1 key || return 1
  if [ "$key" != $'\033' ]; then UI_KEY="$key"; return 0; fi
  while :; do
    tail=''
    IFS= read -rsn1 -t "$escape_timeout" tail || :
    [ "$tail" = $'\033' ] || break
  done
  if [ -z "$tail" ]; then UI_KEY=ESC; return 0; fi
  UI_KEY=OTHER
  if [ "$tail" = '[' ] || [ "$tail" = O ]; then
    sequence=''
    while :; do
      final=''
      IFS= read -rsn1 -t 1 final || break
      [ "${#sequence}" -ge 64 ] || sequence="$sequence$final"
      case '@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\]^_`abcdefghijklmnopqrstuvwxyz{|}~' in
        *"$final"*) break ;;
      esac
    done
    case "$sequence" in
      A) UI_KEY=UP ;; B) UI_KEY=DOWN ;;
      C) UI_KEY=RIGHT ;; D) UI_KEY=LEFT ;;
    esac
  fi
}
ui_pause() {
  [ -t 0 ] && [ -t 1 ] || return 0
  printf '\nEnter / Esc / ← 返回菜单…'
  while ui_read_key; do
    case "$UI_KEY" in ''|ESC|LEFT) printf '\n'; return 0 ;; esac
  done
}
# stty size 仅查询窗口尺寸，不改变终端状态；不可用时使用保守的 80×24。
ui_dimensions() {
  local size rows columns
  rows="${LINES:-24}"; columns="${COLUMNS:-80}"
  size=$(stty size 2>/dev/null) || size=''
  if [ -n "$size" ]; then
    read -r rows columns <<< "$size"
  fi
  case "$rows" in ''|*[!0-9]*|0) rows=24 ;; esac
  case "$columns" in ''|*[!0-9]*|0) columns=80 ;; esac
  UI_ROWS="$rows"; UI_COLUMNS="$columns"
}
ui_brand() {
  if [ "$UI_COLUMNS" -ge 100 ] && [ "$UI_ROWS" -ge 28 ]; then
    printf '\033[1;96m'
    printf '  ███╗   ███╗ █████╗  ██████╗   \033[94m █████╗ ██╗   \033[93m██╗  ██╗██╗████████╗\n\033[96m'
    printf '  ████╗ ████║██╔══██╗██╔════╝   \033[94m██╔══██╗██║   \033[93m██║ ██╔╝██║╚══██╔══╝\n\033[96m'
    printf '  ██╔████╔██║███████║██║        \033[94m███████║██║   \033[93m█████╔╝ ██║   ██║   \n\033[96m'
    printf '  ██║╚██╔╝██║██╔══██║██║        \033[94m██╔══██║██║   \033[93m██╔═██╗ ██║   ██║   \n\033[96m'
    printf '  ██║ ╚═╝ ██║██║  ██║╚██████╗   \033[94m██║  ██║██║   \033[93m██║  ██╗██║   ██║   \n'
  elif [ "$UI_COLUMNS" -ge 64 ] && [ "$UI_ROWS" -ge 22 ]; then
    printf '\033[1;96m'
    printf '  █▄ ▄█ ▄▀▀▄ ▄▀▀▀   \033[94m▄▀▀▄ █   \033[93m█ ▄▀ █ ▀▀█▀▀\n\033[96m'
    printf '  █ ▀ █ █▄▄█ █      \033[94m█▄▄█ █   \033[93m█▀▄  █   █  \n\033[96m'
    printf '  █   █ █  █ ▀▄▄▄   \033[94m█  █ █   \033[93m█  █ █   █  \n'
  else
    printf '  \033[1;96m◆ \033[94m◆ \033[93m◆\n'
  fi
  # 图形、产品名、中文定位各占一层；空行是真实的留白，不依赖颜色区分。
  printf '\033[0m\n  \033[1;97mMac AI Kit\033[0m\n\n  \033[93m本地 AI 创作工坊\033[0m\n'
}
# 窄窗口的当前选项说明按显示列折行；ASCII 一列，中文及其他非 ASCII 保守按两列。
# 不截断说明，也不调用额外依赖；保留 UTF-8 locale 以便按字符切片。
ui_detail() {
  local text="$1" char code width used=0 limit=$((UI_COLUMNS - 4))
  printf '    \033[37m'
  while [ -n "$text" ]; do
    char="${text:0:1}"; text="${text:1}"
    printf -v code '%d' "'$char"
    if [ "$code" -ge 0 ] && [ "$code" -lt 128 ]; then width=1; else width=2; fi
    if [ $((used + width)) -gt "$limit" ]; then
      printf '\n    '; used=0
    fi
    printf '%s' "$char"; used=$((used + width))
  done
  printf '\033[0m\n'
}
# 选项标题也按终端显示列折行，避免较长的中文括号说明越界。
ui_label() {
  local text="$1" char code width used=0 limit=$((UI_COLUMNS - 8))
  [ "$limit" -ge 2 ] || limit=2
  while [ -n "$text" ]; do
    char="${text:0:1}"; text="${text:1}"
    printf -v code '%d' "'$char"
    if [ "$code" -ge 0 ] && [ "$code" -lt 128 ]; then width=1; else width=2; fi
    if [ $((used + width)) -gt "$limit" ]; then
      printf '\n       '; used=0
    fi
    printf '%s' "$char"; used=$((used + width))
  done
}
ui_render() {
  local title="$1" selected="$2" index=0 label description visual=0 compact=0 details=0
  shift 2
  if ui_is_visual; then
    visual=1
    ui_dimensions
    [ "$UI_COLUMNS" -ge 64 ] || compact=1
    [ "$UI_COLUMNS" -ge 64 ] && [ "$UI_ROWS" -ge 28 ] && details=1
    printf '\033[2J\033[H'
    ui_brand
    printf '\n  \033[1;93m◆ \033[97m%s\033[0m\n' "$title"
    [ "$UI_ROWS" -le 18 ] || printf '\n'
  else
    printf '\nMac AI Kit · 本地 AI 创作工坊\n%s\n' "$title"
  fi
  while [ "$#" -gt 1 ]; do
    label="$1"; description="$2"; shift 2
    index=$((index + 1))
    if [ "$visual" -eq 1 ]; then
      if [ "$selected" -eq "$index" ]; then
        printf '  \033[1;97;44m> %s  ' "$index"
        ui_label "$label"
        printf ' \033[0m\n'
      else
        printf '    \033[1;93m%s  \033[97m' "$index"
        ui_label "$label"
        printf '\033[0m\n'
      fi
      # 常规窗口只展开当前项，为品牌留白；大窗口展示全部说明。
      if [ "$details" -eq 1 ] || [ "$selected" -eq "$index" ]; then
        ui_detail "$description"
      fi
    else
      printf '    %s  %s\n       %s\n' "$index" "$label" "$description"
    fi
  done
  if [ "$visual" -eq 1 ]; then
    if [ "$compact" -eq 1 ]; then
      printf '\n  \033[96m↑↓ 移动 · Enter/→ 确认\n  Esc/← 返回 · 数字定位\n  h 帮助 · 0/q %s\033[0m\n' "$UI_BACK_LABEL"
    else
      printf '\n  \033[96m↑↓ 移动  Enter/→ 确认  Esc/← 返回\n  \033[37m数字定位 · 回车确认  h 帮助  0/q %s\033[0m\n' "$UI_BACK_LABEL"
    fi
    [ "$selected" -ne -1 ] || printf '  \033[1;93m> 帮助（按 Enter 确认）\033[0m\n'
    [ "$selected" -ne 0 ] || printf '  \033[1;93m> %s（按 Enter 确认）\033[0m\n' "$UI_BACK_LABEL"
  else
    printf '  h 帮助 · 0/q %s\n请选择编号：' "$UI_BACK_LABEL"
  fi
}
ui_select() {
  local title="$1" selected=1 count key
  shift
  count=$(($# / 2))
  UI_CHOICE=''
  while :; do
    ui_render "$title" "$selected" "$@"
    if [ ! -t 0 ] || [ ! -t 1 ]; then
      IFS= read -r UI_CHOICE || { printf '输入已结束，退出。\n'; UI_CHOICE=0; }
      return 0
    fi
    ui_read_key || { UI_CHOICE=0; return 0; }
    key="$UI_KEY"
    case "$key" in
      ESC) if [ "$UI_BACK_LABEL" != 退出 ]; then UI_CHOICE=0; return 0; fi ;;
      LEFT) UI_CHOICE=0; return 0 ;;
      UP) selected=$((selected - 1)); [ "$selected" -gt 0 ] || selected="$count" ;;
      DOWN) selected=$((selected + 1)); [ "$selected" -le "$count" ] && [ "$selected" -ge 1 ] || selected=1 ;;
      RIGHT) UI_CHOICE="$selected"; [ "$selected" -ne -1 ] || UI_CHOICE=h; return 0 ;;
      '') UI_CHOICE="$selected"; [ "$selected" -ne -1 ] || UI_CHOICE=h; return 0 ;;
      [1-9])
        # 数字只定位，Enter 确认；不会把“1 + Enter”泄漏给下一级菜单。
        [ "$key" -gt "$count" ] || selected="$key" ;;
      0|q) selected=0 ;;
      h) selected=-1 ;;
      a|d|t|r|R) UI_CHOICE="$key"; return 0 ;;
    esac
  done
}
