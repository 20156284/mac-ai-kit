#!/bin/bash
# 无 Python 依赖，兼容 Bash 3.2；仅规范输入字符串，不修改文件。
trim_path_input() {
  local clean="$1"
  # 与 Python str.strip() 的 Unicode 空白集合一致，包括制表符。
  local whitespace=($'\040' $'\011' $'\012' $'\015' $'\013' $'\014' $'\034' $'\035' $'\036' $'\037' $'\302\205' $'\302\240' $'\341\232\200' $'\342\200\200' $'\342\200\201' $'\342\200\202' $'\342\200\203' $'\342\200\204' $'\342\200\205' $'\342\200\206' $'\342\200\207' $'\342\200\210' $'\342\200\211' $'\342\200\212' $'\342\200\250' $'\342\200\251' $'\342\200\257' $'\342\201\237' $'\343\200\200') space changed
  changed=1
  while [ "$changed" -eq 1 ]; do
    changed=0
    for space in "${whitespace[@]}"; do
      case "$clean" in "$space"*) clean="${clean#"$space"}"; changed=1 ;; esac
      case "$clean" in *"$space") clean="${clean%"$space"}"; changed=1 ;; esac
    done
  done
  TRIMMED_PATH="$clean"
}

validate_model_root_shell() {
  trim_path_input "$1"
  local original="$TRIMMED_PATH" rest="$TRIMMED_PATH" part clean suggestion='' invalid=0 more=1 has_component=0
  VALIDATED_MODEL_ROOT=""
  while [ "$more" -eq 1 ]; do
    case "$rest" in */*) part="${rest%%/*}"; rest="${rest#*/}" ;; *) part="$rest"; more=0 ;; esac
    case "$part" in ''|.) ;; *) has_component=1 ;; esac
    clean="$part"
    trim_path_input "$part"
    clean="$TRIMMED_PATH"
    [ "$clean" = "$part" ] || invalid=1
    suggestion="$suggestion$clean"
    [ "$more" -eq 0 ] || suggestion="$suggestion/"
    if [ "$part" = '..' ]; then printf '模型目录不能包含 ..：%q\n' "$original" >&2; return 2; fi
  done
  if [ "$invalid" -ne 0 ]; then
    printf '模型目录组件首尾含空白：%q\n建议相邻规范路径：%q；请显式重新选择。不会自动改名、迁移或删除旧目录。\n' "$original" "$suggestion" >&2
    return 2
  fi
  case "$original" in ''|/|*$'\n'*|*$'\r'*) printf '模型目录无效：%q\n' "$original" >&2; return 2 ;; esac
  if [ "$has_component" -eq 0 ] && [[ "$original" = /* ]]; then
    printf '不能使用文件系统根目录：%q\n' "$original" >&2; return 2
  fi
  VALIDATED_MODEL_ROOT="$original"
}
