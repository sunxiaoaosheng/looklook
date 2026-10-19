# looklook 的 Downie 完成回报脚本（多工作区版）
# 用法：Downie 设置 → 后期处理 → 选「自定义」→「编辑自定义脚本…」，把编辑框内容整体替换为本文件内容。
# Downie 用 /bin/sh 调用本脚本，$1 = 下载完成的文件绝对路径；脚本运行在 Downie 沙盒内（$HOME 被改写，必须用绝对路径）。
# 规则：文件落在 Downie 默认下载目录（手动下载）时不回报；落在其他任何目录（looklook 通过 destination 指定的
# 结果根目录，可以是任意 agent 的工作区）时，在文件旁写可见的完成信号，内容为成品绝对路径。

DOWNIE_DEFAULT_DIR="${LOOKLOOK_DOWNIE_DEFAULT_DIR:-__LOOKLOOK_DOWNLOADS__}"
F="$1"
D=$(dirname "$F")

case "$D" in
  "$DOWNIE_DEFAULT_DIR"|"$DOWNIE_DEFAULT_DIR"/*) exit 0 ;;
esac

M="$D/临时-downie完成-$(date +%Y%m%d-%H%M%S)-$$.txt"
# 先写 .part 再改名，保证 looklook 读到的完成信号是完整的
printf '%s\n' "$F" > "$M.part" && mv "$M.part" "$M"
