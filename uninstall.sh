#!/usr/bin/env bash
# 卸载 codex-quota-resumer 的安装痕迹（服务 + 软链）。
#
# 默认保留运行期数据 ~/.codex/quota-resumer/（config/state/日志），
# 加 --purge-data 才会一并删除。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DST="$HOME/.local/bin/codex-quota-resumer"
UNIT_DST="$HOME/.config/systemd/user/codex-quota-resumer.service"
UNIT_NAME="codex-quota-resumer.service"
DATA_DIR="$HOME/.codex/quota-resumer"

PURGE=0
[ "${1:-}" = "--purge-data" ] && PURGE=1

say() { printf '  %s\n' "$*"; }

echo "卸载 codex-quota-resumer"

if systemctl --user list-unit-files "$UNIT_NAME" >/dev/null 2>&1; then
  systemctl --user stop "$UNIT_NAME" 2>/dev/null || true
  systemctl --user disable "$UNIT_NAME" 2>/dev/null || true
  say "已停止并禁用 $UNIT_NAME"
fi

# 只删指向本仓库的软链，避免误删别人装的东西
for pair in "$UNIT_DST:$REPO" "$BIN_DST:$REPO"; do
  dst="${pair%%:*}"; prefix="${pair#*:}"
  if [ -L "$dst" ]; then
    target="$(readlink -f "$dst" || true)"
    case "$target" in
      "$prefix"/*) rm -f "$dst"; say "已移除软链 $dst" ;;
      *)           say "保留 $dst（指向 $target，不属于本仓库）" ;;
    esac
  elif [ -e "$dst" ]; then
    say "保留 $dst（真实文件，非软链）"
  fi
done

systemctl --user daemon-reload 2>/dev/null || true

if [ "$PURGE" = "1" ]; then
  rm -rf "$DATA_DIR"
  say "已删除运行期数据 $DATA_DIR"
else
  say "运行期数据保留在 $DATA_DIR（加 --purge-data 可删除）"
fi

echo "完成。"
