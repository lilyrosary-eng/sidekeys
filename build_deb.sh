#!/usr/bin/env bash
# 组一个开箱即用的 .deb：不需要 debhelper，直接用 dpkg-deb 组装目录树。
#
#   ./build_deb.sh              # 版本取自 CHANGELOG 最新的数字标题
#   ./build_deb.sh 0.4.0        # 指定版本
#
# 为什么不用 /usr/local：Debian 规定包不能往 /usr/local 写（那是管理员的地盘），
# 而且 polkit 授权的可执行文件必须 root 属主 —— 所以全程用 --root-owner-group，
# 构建结束还会把属主读回来检查一遍。
set -euo pipefail
cd "$(dirname "$0")"

REPO=$(git rev-parse --show-toplevel)
PKG=sidekeys
ARCH=$(dpkg --print-architecture)
VERSION=${1:-}
[ -n "$VERSION" ] || VERSION=$(awk '/^## [0-9]/{gsub(/^## /,"");print $1; exit}' CHANGELOG.md)
[ -n "$VERSION" ] || { echo "定不出版本号：CHANGELOG 里没有数字标题" >&2; exit 1; }

BUILD=$REPO/build/deb
OUT=$REPO/dist
DEB=$OUT/${PKG}_${VERSION}_${ARCH}.deb

echo "== 1. 组树 $BUILD =="
rm -rf "$BUILD"
mkdir -p "$BUILD/DEBIAN" \
         "$BUILD/usr/bin" \
         "$BUILD/usr/lib/sidekeys" \
         "$BUILD/usr/lib/systemd/system/keyd.service.d" \
         "$BUILD/usr/lib/udev/rules.d" \
         "$BUILD/usr/lib/modules-load.d" \
         "$BUILD/usr/share/polkit-1/actions" \
         "$BUILD/usr/share/applications" \
         "$BUILD/usr/share/doc/$PKG"

install -m 0755 src/sidekeys.py "$BUILD/usr/bin/sidekeys"
install -m 0644 src/sidekeys_validate.py "$BUILD/usr/lib/sidekeys/sidekeys_validate.py"
install -m 0755 src/sidekeys-apply "$BUILD/usr/lib/sidekeys/sidekeys-apply"
install -m 0755 contrib/sidekeys-run "$BUILD/usr/lib/sidekeys/sidekeys-run"
install -m 0644 contrib/sidekeys.service "$BUILD/usr/lib/systemd/system/sidekeys.service"
install -m 0644 contrib/10-keyd-exclusivity.conf \
  "$BUILD/usr/lib/systemd/system/keyd.service.d/10-sidekeys.conf"
install -m 0644 contrib/99-sidekeys-uinput.rules "$BUILD/usr/lib/udev/rules.d/99-sidekeys-uinput.rules"
install -m 0644 data/org.andeyunhui.sidekeys.policy \
  "$BUILD/usr/share/polkit-1/actions/org.andeyunhui.sidekeys.policy"
printf 'uinput\n' > "$BUILD/usr/lib/modules-load.d/$PKG-uinput.conf"
chmod 0644 "$BUILD/usr/lib/modules-load.d/$PKG-uinput.conf"
sed 's|@BINDIR@|/usr/bin|' data/com.andeyunhui.sidekeys.desktop.in \
  > "$BUILD/usr/share/applications/com.andeyunhui.sidekeys.desktop"
chmod 0644 "$BUILD/usr/share/applications/com.andeyunhui.sidekeys.desktop"
install -m 0644 README.md CHANGELOG.md THIRD-PARTY-NOTICES.md "$BUILD/usr/share/doc/$PKG/"
install -m 0644 LICENSE "$BUILD/usr/share/doc/$PKG/license"

# d/copyright（DEP-5）：许可证正文直接从 LICENSE 生成，避免我手抄错字
{
  printf 'Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n'
  printf 'Upstream-Name: %s\n' "$PKG"
  printf 'Upstream-Contact: https://github.com/lilyrosary-eng/sidekeys\n'
  printf 'Source: https://github.com/lilyrosary-eng/sidekeys\n\n'
  printf 'Files: *\n'
  printf 'Copyright: 2026 lilyrosary-eng\n'
  printf 'License: MIT\n\n'
  printf 'License: MIT\n'
  sed 's/^ /./; s/^/ /' LICENSE
} > "$BUILD/usr/share/doc/$PKG/copyright"
chmod 0644 "$BUILD/usr/share/doc/$PKG/copyright"

for f in postinst prerm postrm; do install -m 0755 "packaging/$f" "$BUILD/DEBIAN/$f"; done

echo "== 2. control 与 Installed-Size =="
SIZE=$(du -sk "$BUILD" | cut -f1)
sed -e "s/SIDEKEYS_PACKAGE/$PKG/" -e "s/SIDEKEYS_VERSION/$VERSION/" \
    -e "/^Installed-Size:/d" packaging/control > "$BUILD/DEBIAN/control"
printf 'Installed-Size: %s\n' "$SIZE" >> "$BUILD/DEBIAN/control"
# Description 之后的字段必须插在末尾前先确认结构没被写坏
grep -q '^Package: ' "$BUILD/DEBIAN/control" || { echo "control 生成失败" >&2; exit 1; }

echo "== 3. md5sums =="
( cd "$BUILD" && find usr -type f -print0 | xargs -0 md5sum ) > "$BUILD/DEBIAN/md5sums"

echo "== 4. 一致性自检（打包最容易错的就是“路径写了但没装”） =="
# polkit 的 exec.path 必须指向包里真实存在、且 root 可执行的文件
EXEC_PATH=$(grep -o 'policykit.exec.path">[^<]*' \
  "$BUILD/usr/share/polkit-1/actions/org.andeyunhui.sidekeys.policy" | sed 's/.*">//')
case "$EXEC_PATH" in
  /*) [ -f "$BUILD$EXEC_PATH" ] || { echo "✗ 策略里的 exec.path $EXEC_PATH 不在包里" >&2; exit 1; } ;;
  *) echo "✗ exec.path 不是绝对路径：$EXEC_PATH" >&2; exit 1 ;;
esac
echo "   ✓ polkit exec.path = $EXEC_PATH（包内存在）"
# 单元的 ExecStart 同样必须真实存在
for field in ExecStart ExecStartPre; do
  while read -r cmd; do
    path=${cmd#+}; path=${path%% *}
    case "$path" in
      /*) [ -f "$BUILD$path" ] || echo "   ! 单元里的 $field 指向 $path，不在包内（可能属于系统）" ;;
    esac
  done < <(sed -n "s/^${field}=//p" "$BUILD/usr/lib/systemd/system/sidekeys.service")
done
grep -q '^ExecStart=/usr/lib/sidekeys/sidekeys-run$' "$BUILD/usr/lib/systemd/system/sidekeys.service" \
  || { echo "✗ 单元 ExecStart 不是规范路径" >&2; exit 1; }
echo "   ✓ 单元 ExecStart 指向 /usr/lib/sidekeys/sidekeys-run"
# GUI 解析 root 助手的候选顺序里，规范路径必须排第一，包内三份 root 侧文件必须齐备。
# 这一步是真跑代码：导入包内那份 sidekeys.py，读它自己算出来的候选表，
# 而不是在 shell 里用字符串猜。PYTHONDONTWRITEBYTECODE 是必须的：不加的话
# import 会往构建树里写 __pycache__，那一版就把 .pyc 打进包里了。
PYTHONDONTWRITEBYTECODE=1 python3 - "$BUILD" <<'PYCHK'
import os
import sys
from importlib.machinery import SourceFileLoader

build = sys.argv[1]
sk = SourceFileLoader("sk_packaged", os.path.join(build, "usr/bin/sidekeys")).load_module()
want = "/usr/lib/sidekeys/sidekeys-apply"
if sk.APPLY_HELPER_CANDIDATES[0] != want:
    raise SystemExit(f"✗ 助手候选顺序不对：{sk.APPLY_HELPER_CANDIDATES}")
missing = [f for f in (want, "/usr/lib/sidekeys/sidekeys-run", "/usr/lib/sidekeys/sidekeys_validate.py")
           if not os.path.isfile(build + f)]
if missing:
    raise SystemExit(f"✗ 包内缺少：{missing}")
if "/usr/lib/sidekeys/sidekeys_validate.py" not in sk.VALIDATOR_PATHS:
    raise SystemExit(f"✗ 校验器候选里没有规范路径：{sk.VALIDATOR_PATHS}")
print(f"   ✓ GUI 助手候选第一位是 {want}；三份 root 侧文件齐备；校验器候选含规范路径")
PYCHK

echo "== 5. 归一化权限并构建 =="
# ① 校验步骤的 import 会往树里留 __pycache__（第一次构建就把 .pyc 装进了包），打包前清掉；
# ② 目录继承工作区会带上 0775（组可写），统一成 0755。
find "$BUILD" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$BUILD" -type d -exec chmod 0755 {} +
mkdir -p "$OUT"
rm -f "$DEB"
dpkg-deb --build --root-owner-group "$BUILD" "$DEB"

echo "== 6. 产物校验 =="
ls -l "$DEB"
dpkg-deb -f "$DEB" Package Version Architecture Installed-Size Depends
echo "   --- 包内文件（前 12 行）---"
dpkg-deb -c "$DEB" | sed -n '1,12p'
# 包内文件必须 root 属主：polkit 授权执行的脚本若用户可写，就是提权入口。
# dpkg-deb -c 的列依次是 mode、uid/gid、大小、日期、时间、名字 —— 别拿错列。
OWNER=$(dpkg-deb -c "$DEB" | awk '{print $2}' | sort -u | tr '\n' ' ')
case "$OWNER" in
  "root/root ") echo "   ✓ 包内全部条目属主 root/root" ;;
  *) echo "   ✗ 出现非 root 属主：$OWNER" >&2; exit 1 ;;
esac
# 权限串第 6 位是组的 w、第 9 位是其他的 w（第 1 位是类型）。
# 之前写的是 7 与 9，把“组的其他位”当成了“组的写位”，于是 0775 目录一路放行。
WRITABLE=$(dpkg-deb -c "$DEB" | awk 'substr($1,6,1)=="w" || substr($1,9,1)=="w" {print $1, $NF}' | head -5)
if [ -n "$WRITABLE" ]; then
  echo "   ✗ 有组/其他可写条目（root 执行的东西不能让别人改）：" >&2
  echo "$WRITABLE" >&2
  exit 1
fi
echo "   ✓ 没有组/其他可写条目"
PYC=$(dpkg-deb -c "$DEB" | grep -c -e __pycache__ -e '[.]pyc' || true)
if [ "$PYC" != 0 ]; then
  echo "   ✗ 包里混进了 $PYC 个 __pycache__/.pyc 条目" >&2
  exit 1
fi
echo "   ✓ 没有 __pycache__/.pyc"
echo "   ✓ 已完成；安装：sudo apt install ./dist/${PKG}_${VERSION}_${ARCH}.deb"
