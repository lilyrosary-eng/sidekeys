#!/usr/bin/env bash
# 卸载 sidekeys。默认保留 /etc/xremap 下的配置（万一你还要手动用 xremap）。
#
#   sudo ./uninstall.sh              卸载系统侧 + 当前用户侧 GUI，保留配置
#   sudo ./uninstall.sh --purge      连 /etc/xremap 的配置一起删
#   ./uninstall.sh --user-only       只删 GUI 与桌面入口

set -euo pipefail

UNIT=sidekeys
PURGE=0
USER_ONLY=0

# 与 install.sh 同样的目标用户判定：sudo 保留 HOME，pkexec 会把 HOME 变成 /root
TARGET_USER="${SUDO_USER:-}"
if [ -z "$TARGET_USER" ] && [ -n "${PKEXEC_UID:-}" ]; then
  TARGET_USER="$(id -un "$PKEXEC_UID" 2>/dev/null || true)"
fi
[ -n "$TARGET_USER" ] || TARGET_USER="$(id -un)"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
DEST_BIN="${TARGET_HOME}/.local/bin"
DEST_APPS="${TARGET_HOME}/.local/share/applications"
for a in "$@"; do
  case "$a" in
    --purge) PURGE=1 ;;
    --user-only) USER_ONLY=1 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "未知参数：$a" >&2; exit 2 ;;
  esac
done

echo "== 用户侧 =="
rm -f "$DEST_BIN/sidekeys"
rm -f "$DEST_APPS/com.andeyunhui.sidekeys.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$DEST_APPS" 2>/dev/null || true
echo "   已移除 GUI 与桌面入口"

if [ "$USER_ONLY" = 1 ]; then
  echo
  echo "--user-only：系统侧未改动（服务仍在运行）。要卸系统侧：sudo ./uninstall.sh"
  exit 0
fi

[ "$(id -u)" = 0 ] || { echo "需要 root：sudo ./uninstall.sh" >&2; exit 1; }

echo
echo "== 系统侧 =="
systemctl stop "$UNIT" 2>/dev/null || true
systemctl disable "$UNIT" 2>/dev/null || true
# 只有本工具确实接管过 keyd（写过我们的配置文件）才去碰它：
# keyd 可能是用户为自己键盘装的，不能顺手 disable。
if [ -f /etc/keyd/sidekeys.conf ]; then
  systemctl disable --now keyd 2>/dev/null || true
  rm -f /etc/keyd/sidekeys.conf /etc/keyd/sidekeys.conf.bak
  echo "已移除本工具写入的 /etc/keyd/sidekeys.conf 并停止 keyd；侧键恢复鼠标默认行为"
fi
rm -f "/etc/systemd/system/${UNIT}.service"
rm -f /etc/systemd/system/keyd.service.d/10-exclusivity.conf
rmdir /etc/systemd/system/keyd.service.d 2>/dev/null || true
# 装成 deb 的时候这些文件属于 dpkg，手删会留下半个包：交给 apt remove 处理。
if dpkg -S /usr/lib/sidekeys/sidekeys-apply >/dev/null 2>&1; then
  echo "   检测到 sidekeys 是以 deb 安装的，请改用：sudo apt remove sidekeys"
else
  rm -f /usr/lib/sidekeys/sidekeys-apply /usr/lib/sidekeys/sidekeys-run \
        /usr/lib/sidekeys/sidekeys_validate.py
  rmdir /usr/lib/sidekeys 2>/dev/null || true
  rm -f /usr/local/bin/sidekeys-apply /usr/local/bin/sidekeys-run
  rm -f /usr/local/lib/sidekeys/sidekeys_validate.py
  rmdir /usr/local/lib/sidekeys 2>/dev/null || true
fi
rm -rf /usr/local/lib/sidekeys
rm -f /usr/share/polkit-1/actions/org.andeyunhui.sidekeys.policy
rm -f /etc/udev/rules.d/99-sidekeys-uinput.rules
# 旧版可能把 uinput 写进过 /etc/modules-load.d/uinput.conf；那文件也可能属于别的包，
# 只有内容确实只有我们那一行时才删。
legacy_ml=/etc/modules-load.d/uinput.conf
if [ -f "$legacy_ml" ] && grep -qx uinput "$legacy_ml" && [ "$(grep -cv '^\s*$' "$legacy_ml")" = 1 ]; then
  rm -f "$legacy_ml"
fi
rm -f /etc/modules-load.d/sidekeys-uinput.conf
systemctl daemon-reload
udevadm control --reload-rules
echo "   已移除服务、助手、polkit 策略与 udev 规则"

if [ "$PURGE" = 1 ]; then
  rm -rf /etc/xremap
  echo "   已删除 /etc/xremap（--purge）"
else
  echo "   保留 /etc/xremap（含 config.yml 与备份）；要一并删掉：sudo ./uninstall.sh --purge"
fi

if id xremap >/dev/null 2>&1; then
  echo
  echo "专用用户 xremap 仍在系统里。确认不再需要手动跑 xremap 后可删："
  echo "   sudo userdel xremap"
  echo "（xremap 本体二进制 $([ -x /usr/bin/xremap ] && echo 已安装 || echo 未安装)；本项目不打包它，需自行处理）"
fi

echo
echo "卸载完成。侧键已恢复鼠标默认行为。"
