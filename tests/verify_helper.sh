#!/usr/bin/env bash
# root 助手的静态回归：盯住“配置写了但进程没重载”这一类 bug。
# 线上事故：apply_keyd 用 `systemctl enable --now`，对已在运行的单元是空操作，
# 于是用户改了 C-a、磁盘上是新的、keyd 还在发旧的回车。keyd 自己不监听配置文件。
set -uo pipefail
cd "$(dirname "$0")/.."
H=src/sidekeys-apply
fail=0

check() { # $1=描述 $2=条件(0/1)
  if [ "$2" = 1 ]; then printf '  PASS  %s\n' "$1"; else printf '  FAIL  %s\n' "$1"; fail=1; fi
}

bash -n "$H"; check "bash 语法" "$([ $? = 0 ] && echo 1 || echo 0)"
grep -q '^bring_up()' "$H"; check "有 bring_up（已在跑就 restart，而不是 enable --now）" "$([ $? = 0 ] && echo 1 || echo 0)"

# apply_keyd 与两个 start 分支都必须走 bring_up
n=$(awk '/^apply_keyd\(\)/,/^}/' "$H" | grep -c 'bring_up "\$KEYD_UNIT"')
check "apply_keyd 走 bring_up" "$([ "$n" -ge 1 ] && echo 1 || echo 0)"
n=$(grep -c 'bring_up "\$KEYD_UNIT"\|bring_up "\$UNIT"' "$H")
check "start 的两个分支也走 bring_up（共 >=3 处）" "$([ "$n" -ge 3 ] && echo 1 || echo 0)"

# 事故形态不能再出现：apply/start 路径里不允许 enable --now（注释里提到它不算）
n=$(grep -v '^[[:space:]]*#' "$H" | grep -c 'systemctl enable --now')
check "没有残留的 enable --now（对已运行单元是空操作）" "$([ "$n" = 0 ] && echo 1 || echo 0)"

grep -q 'keyd_parse_count' "$H"; check "有“真的重新解析了配置”的运行时证据" "$([ $? = 0 ] && echo 1 || echo 0)"
n=$(awk '/^apply_keyd\(\)/,/^}/' "$H" | grep -c 'parses_after\|parses_before')
check "apply_keyd 比对重载前后的解析次数" "$([ "$n" -ge 3 ] && echo 1 || echo 0)"
grep -q '没有重新解析配置' "$H"; check "重载没发生时会回滚并明确报错" "$([ $? = 0 ] && echo 1 || echo 0)"

# xremap 分支本来就用 restart，别在重构里弄丢
n=$(awk '/^apply_xremap\(\)/,/^}/' "$H" | grep -c 'systemctl restart "\$UNIT"')
check "apply_xremap 仍然 restart" "$([ "$n" -ge 1 ] && echo 1 || echo 0)"

echo
[ "$fail" = 0 ] && echo "结论: ✓ 通过" || echo "结论: ✗ 见上"
exit "$fail"
