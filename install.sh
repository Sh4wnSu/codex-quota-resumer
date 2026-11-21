#!/usr/bin/env bash
# 安装 codex-quota-resumer：把源码软链到 PATH 与 systemd，并启用常驻服务。
#
# 设计：仓库是唯一源码，~/.local/bin 与 ~/.config/systemd/user 只放软链，
#       这样 "改代码 → restart 服务" 就生效，不需要再拷贝。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_SRC="$REPO/bin/codex-quota-resumer"
UNIT_SRC="$REPO/systemd/codex-quota-resumer.service"
BIN_DST="$HOME/.local/bin/codex-quota-resumer"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_DST="$UNIT_DIR/codex-quota-resumer.service"
UNIT_NAME="codex-quota-resumer.service"

say() { printf '  %s\n' "$*"; }
fail() { printf '错误: %s\n' "$*" >&2; exit 1; }

[ -f "$BIN_SRC" ] || fail "找不到 $BIN_SRC"
[ -f "$UNIT_SRC" ] || fail "找不到 $UNIT_SRC"
command -v systemctl >/dev/null || fail "没有 systemctl，无法安装为服务"
command -v python3 >/dev/null || fail "需要 python3"

echo "安装 codex-quota-resumer"
echo "  仓库: $REPO"

# 1) 可执行文件软链
mkdir -p "$(dirname "$BIN_DST")"
chmod +x "$BIN_SRC"
ln -sfn "$BIN_SRC" "$BIN_DST"
say "已链接 $BIN_DST -> $BIN_SRC"

# 2) systemd user unit 软链
mkdir -p "$UNIT_DIR"
ln -sfn "$UNIT_SRC" "$UNIT_DST"
say "已链接 $UNIT_DST -> $UNIT_SRC"

# 3) 重载并启用
systemctl --user daemon-reload
systemctl --user enable "$UNIT_NAME" || fail "未能启用开机或登录时自动启动: $UNIT_NAME"
systemctl --user restart "$UNIT_NAME"
sleep 3

if systemctl --user is-active --quiet "$UNIT_NAME"; then
  say "服务已启动: $UNIT_NAME"
else
  fail "服务未能启动，查看: journalctl --user -u $UNIT_NAME -n 50"
fi

echo
echo "自检："
"$BIN_DST" --probe 2>&1 | sed 's/^/  /' || true

echo
echo "完成。日常命令："
echo "  codex-quota-resumer --status"
echo "  journalctl --user -u $UNIT_NAME -f"
