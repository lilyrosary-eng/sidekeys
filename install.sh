#!/usr/bin/env bash
# 安装 sidekeys（鼠标侧键映射，基于 xremap）。
#
#   sudo ./install.sh            完整安装：root 侧服务 + 当前用户侧 GUI
#   ./install.sh --user-only     只装 GUI 与桌面入口（不动系统，适合先看界面）
#   sudo ./install.sh --user-only 会被拒绝：--user-only 不需要 root
#
# 幂等：重复执行不会覆盖你已有的 /etc/xremap/config.yml。

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_ONLY=0
for a in "$@"; do
  case "$a" in
    --user-only) USER_ONLY=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "未知参数：$a" >&2; exit 2 ;;
  esac
done

UID_NUM="$(id -u)"

# 用户侧要装到“发起安装的那个人”的家目录：sudo 会保留 HOME，pkexec 会把 HOME 变成 /root，
# 所以不能直接信 $HOME。依次尝试 SUDO_USER、PKEXEC_UID，再退回当前用户。
TARGET_USER="${SUDO_USER:-}"
if [ -z "$TARGET_USER" ] && [ -n "${PKEXEC_UID:-}" ]; then
  TARGET_USER="$(id -un "$PKEXEC_UID" 2>/dev/null || true)"
fi
[ -n "$TARGET_USER" ] || TARGET_USER="$(id -un)"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[ -d "$TARGET_HOME" ] || { echo "找不到 $TARGET_USER 的家目录" >&2; exit 1; }

DEST_BIN="${TARGET_HOME}/.local/bin"
DEST_APPS="${TARGET_HOME}/.local/share/applications"
UNIT=sidekeys
XREMAP_BIN=/usr/bin/xremap
echo "   目标用户：$TARGET_USER（$TARGET_HOME）"

first_mouse() {
  awk 'BEGIN{RS=""}
       /H: +Handlers=/ && !/Touchpad/ && !/devices\/virtual\// {
         if (match($0, /H: +Handlers=[^\n]*\bmouse[0-9]/) && match($0, /N: +Name="[^"]+"/)) {
           print substr($0, RSTART + 9, RLENGTH - 10); exit
         }
       }' /proc/bus/input/devices
}

echo "== 1. 用户侧（GUI + 桌面入口）=="
install -d -m 0755 "$DEST_BIN" "$DEST_APPS"
install -m 0755 "$REPO/src/sidekeys.py" "$DEST_BIN/sidekeys"
# 校验器跟 GUI 同目录放一份：GUI 必须用与自己版本一致的规则，
# 否则旧的系统校验器会把新版生成的合法写法判成“不认识的键”。
install -m 0644 "$REPO/src/sidekeys_validate.py" "$DEST_BIN/sidekeys_validate.py"
sed "s|@BINDIR@|${DEST_BIN}|" "$REPO/data/com.andeyunhui.sidekeys.desktop.in" \
  > "$DEST_APPS/com.andeyunhui.sidekeys.desktop"
chmod 0644 "$DEST_APPS/com.andeyunhui.sidekeys.desktop"
# 以 root 跑时，这些文件落在别人家目录里，必须把属主还给那个用户
if [ "$UID_NUM" = 0 ]; then
  chown "$TARGET_USER" "$DEST_BIN" "$DEST_APPS" "$DEST_BIN/sidekeys" \
    "$DEST_BIN/sidekeys_validate.py" \
    "$DEST_APPS/com.andeyunhui.sidekeys.desktop"
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$DEST_APPS" 2>/dev/null || true
echo "   可执行：$DEST_BIN/sidekeys"

if [ "$USER_ONLY" = 1 ]; then
  echo
  echo "--user-only：系统侧未改动。要装服务请执行  sudo ./install.sh"
  exit 0
fi

echo
echo "== 2. 检查 root 侧前提 =="
[ "$UID_NUM" = 0 ] || { echo "需要 root：sudo ./install.sh（或 --user-only 只装 GUI）" >&2; exit 1; }
[ -x "$XREMAP_BIN" ] || {
  echo "没找到 $XREMAP_BIN。先装 xremap（本项目不打包它）：" >&2
  echo "  curl -fsSL -o /tmp/xremap.zip https://github.com/xremap/xremap/releases/download/v0.15.17/xremap-linux-x86_64-gnome.zip" >&2
  echo "  cd /tmp && unzip -o xremap.zip && install -m 0755 xremap $XREMAP_BIN" >&2
  exit 1
}
command -v setfacl >/dev/null || { echo "缺 setfacl（Ubuntu: apt install acl）" >&2; exit 1; }
# keyd 是可选后端（长按到点即发），没有也能用 xremap，所以这里只提示不中断
if [ ! -x /usr/bin/keyd.rvaiya ] && [ ! -x /usr/bin/keyd ]; then
  echo "提示：没找到 keyd。想用“长按不等松手就发出”的后端时再装：sudo apt install keyd" >&2
fi
command -v python3 >/dev/null || { echo "缺 python3" >&2; exit 1; }

echo
echo "== 3. 专用系统用户（属于 input 组，不登录、无 home）=="
if id xremap >/dev/null 2>&1; then
  echo "   已存在"
else
  useradd --no-create-home --shell /bin/false --user-group --groups input --system xremap
  echo "   已创建"
fi

echo
echo "== 4. 配置与 uinput 权限 =="
install -d -m 0755 /etc/xremap
if [ ! -s /etc/xremap/config.yml ]; then
  DEV="$(first_mouse)"
  [ -n "$DEV" ] || { echo "没检测到鼠标设备；插上鼠标再跑一次" >&2; exit 1; }
  cat > /etc/xremap/config.yml <<EOF
keymap:
  - name: 侧键映射
    device:
      only: ["$DEV"]
    remap:
      BTN_SIDE: C-c
      BTN_EXTRA: C-v
EOF
  chown xremap:xremap /etc/xremap/config.yml
  chmod 0644 /etc/xremap/config.yml
  echo "   生成默认配置（设备：$DEV）"
else
  echo "   已存在，保留不覆盖"
fi

# 设备名以 config.yml 为准同步进环境文件，避免 unit 与实际接管对象不一致
if command -v python3 >/dev/null && python3 -c 'import yaml' 2>/dev/null; then
  DEV="$(python3 - <<'PY'
import yaml
doc = yaml.safe_load(open("/etc/xremap/config.yml", encoding="utf-8")) or {}
for b in doc.get("keymap") or []:
    only = (b.get("device") or {}).get("only") or []
    if only:
        print(only[0]); break
PY
)"
else
  DEV="$(awk '/^ *only:/{f=1;next} f&&/^ *- /{sub(/^ *- /,"");print;exit}' /etc/xremap/config.yml)"
fi
[ -n "$DEV" ] || DEV="$(first_mouse)"
[ -n "$DEV" ] || { echo "仍无法确定设备名；请手动写 /etc/xremap/sidekeys.env" >&2; exit 1; }
printf 'SIDEKEYS_DEVICE="%s"\n' "$DEV" > /etc/xremap/sidekeys.env
chmod 0644 /etc/xremap/sidekeys.env
echo "   接管设备：$DEV"

install -m 0644 "$REPO/contrib/99-sidekeys-uinput.rules" /etc/udev/rules.d/
echo uinput > /etc/modules-load.d/uinput.conf
modprobe uinput 2>/dev/null || true
udevadm control --reload-rules
udevadm trigger --action=change --sysname-match=uinput || true

echo
echo "== 5. 助手、校验器、polkit 策略与 systemd 服务 =="
install -m 0755 "$REPO/src/sidekeys-apply" /usr/local/bin/sidekeys-apply
install -m 0755 "$REPO/contrib/sidekeys-run" /usr/local/bin/sidekeys-run
install -d -m 0755 /usr/local/lib/sidekeys
install -m 0644 "$REPO/src/sidekeys_validate.py" /usr/local/lib/sidekeys/sidekeys_validate.py
install -m 0644 "$REPO/data/org.andeyunhui.sidekeys.policy" /usr/share/polkit-1/actions/
install -m 0644 "$REPO/contrib/sidekeys.service" "/etc/systemd/system/${UNIT}.service"
bash -n /usr/local/bin/sidekeys-apply
bash -n /usr/local/bin/sidekeys-run
python3 -c 'import yaml' 2>/dev/null || { echo "缺 python3-yaml（Ubuntu: apt install python3-yaml）" >&2; exit 1; }
python3 /usr/local/lib/sidekeys/sidekeys_validate.py /etc/xremap/config.yml
"$XREMAP_BIN" --validate-config /etc/xremap/config.yml

systemctl daemon-reload
# 两个引擎互斥：它们都靠 EVIOCGRAB 独占同一只鼠标，同时跑就是一方静默收不到事件。
# apt install keyd / kanata 会自动 enable+start 它们的单元，所以这里显式让位给本工具。
systemctl disable --now keyd >/dev/null 2>&1 || true
systemctl disable --now kanata >/dev/null 2>&1 || true
systemctl enable --now "$UNIT"
sleep 2

echo
echo "== 6. 结果 =="
systemctl --no-pager --lines=6 status "$UNIT" || true
echo
echo "   启动 GUI：$DEST_BIN/sidekeys   （或应用 grid 里的“侧键映射”）"
echo "   停用服务：sudo systemctl stop $UNIT    # 侧键立刻恢复鼠标默认行为"
echo "   卸载：     sudo ./uninstall.sh"
