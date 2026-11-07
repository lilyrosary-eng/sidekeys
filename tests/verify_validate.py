#!/usr/bin/env python3
"""校验器与配置生成的纯逻辑测试（不需要 GUI 会话）。"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import yaml  # noqa: E402

import sidekeys as sk  # noqa: E402
import sidekeys_validate as sv  # noqa: E402

results = []


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    except Exception as exc:
        return f"抛的是 {type(exc).__name__}，不是 ValueError"
    return False


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def errs_of(text):
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        path = fh.name
    try:
        return sv.validate(path)
    finally:
        os.unlink(path)


def cfg(**kw):
    b = {k: {"tap": None, "hold": None, "double": None} for k, _ in sk.BUTTONS}
    for code, gestures in kw.items():
        b[code].update(gestures)
    return sk.dump_yaml(b, "Test Mouse")


# ---------- 1) 合法写法必须不误杀 ----------
LEGAL = [
    ("点按复制", cfg(BTN_SIDE={"tap": "C-c"})),
    ("组合键 Shift-A-Tab", cfg(BTN_SIDE={"tap": "Shift-A-Tab"})),
    ("终端粘贴 S-Insert", cfg(BTN_SIDE={"tap": "S-Insert"})),
    ("锁屏 Super-L", cfg(BTN_SIDE={"tap": "Super-L"})),
    ("屏蔽 []", cfg(BTN_SIDE={"tap": "[]"})),
    ("宏序列 C-c, Enter", cfg(BTN_SIDE={"tap": "C-c, Enter"})),
    ("修饰键别名 Ctrl_L", cfg(BTN_SIDE={"tap": "Ctrl_L"})),
    ("长按剪切", cfg(BTN_SIDE={"tap": "C-c", "hold": "C-x"})),
    ("双击回车", cfg(BTN_SIDE={"tap": "S-Insert", "double": "Enter"})),
    ("三手势齐全", cfg(BTN_SIDE={"tap": "C-c", "hold": "C-x", "double": "Enter"})),
]
for name, text in LEGAL:
    e = errs_of(text)
    check(f"合法不误杀：{name}", not e, "；".join(e))

# ---------- 2) 非法写法必须拦住 ----------
check("拦住 Return 并建议 Enter", any("Enter" in x and "Return" in x for x in errs_of(cfg(BTN_SIDE={"double": "Return"}))))
check("拦住乱造的键名", bool(errs_of(cfg(BTN_SIDE={"hold": "TotallyBogus"}))))
check("拦住不带 BTN_ 前缀的触发键", bool(errs_of(cfg(BTN_SIDE={"tap": "C-c"}).replace("BTN_SIDE", "Side"))))
check("拦住未知修饰键", bool(errs_of(cfg(BTN_SIDE={"tap": "Hyper-x"}))))
check("拦住未知顶层区块", bool(errs_of(cfg(BTN_SIDE={"tap": "C-c"}) + "foobar: 1\n")))
check("拦住空文件", bool(errs_of("")))
check("拦住两个侧键都没绑定", bool(errs_of(cfg())))
# 长按阈值的边界：越界/非整数/free_hold 都必须拦下（free_hold 会让鼠标长按永远不触发）
check("拦住越界的长按阈值", any("tap_timeout_millis" in x for x in
                              errs_of(cfg(BTN_SIDE={"tap": "C-c", "hold": "C-x"})
                                      .replace("tap_timeout_millis: 400", "tap_timeout_millis: 5000"))))
check("拦住非整数的长按阈值", any("tap_timeout_millis" in x for x in
                               errs_of(cfg(BTN_SIDE={"tap": "C-c", "hold": "C-x"})
                                       .replace("tap_timeout_millis: 400", 'tap_timeout_millis: "fast"'))))
check("拦住 free_hold（鼠标键不发 repeat，长按会静默失效）", any("free_hold" in x for x in
      errs_of(cfg(BTN_SIDE={"tap": "C-c", "hold": "C-x"})
              .replace("tap_timeout_millis: 400", "free_hold: true"))))
check("放行范围内的长按阈值", not errs_of(cfg(BTN_SIDE={"tap": "C-c", "hold": "C-x"})
                                .replace("tap_timeout_millis: 400", "tap_timeout_millis: 250")))

# ---------- 2b) modmap 的真实坑（线上事故根因，实测 xremap 会静默忽略）----------
check("拦住 modmap 里的组合字符串", any("数组" in x and "modmap" in x for x in errs_of('''modmap:
  - name: 长按行为
    remap:
      BTN_EXTRA:
        alone: S-Insert
        held: Enter
''')))
check("拦住 modmap 数组里的多个普通键", bool(errs_of('''modmap:
  - remap:
      BTN_EXTRA:
        held: [C, V]
''')))
check("放行 modmap 的正确数组写法", not errs_of('''modmap:
  - remap:
      BTN_EXTRA:
        alone: [SHIFT_L, Insert]
        held: [CTRL_L, C]
'''))

# ---------- 3) 配置生成形状 ----------
def sections(text):
    return set(yaml.safe_load(text))


def remap_of(text, section):
    blocks = yaml.safe_load(text).get(section) or []
    out = {}
    for b in blocks:
        out.update(b.get("remap") or {})
    return out


one = cfg(BTN_SIDE={"tap": "C-c"})
check("只有点按 → 只写 keymap", sections(one) == {"keymap"}, str(sections(one)))
two = cfg(BTN_SIDE={"tap": "C-c", "hold": "C-x"})
check("有点按+长按 → 只写 modmap，不重复写 keymap", sections(two) == {"modmap"}, str(sections(two)))
check("长按生成 alone/held 结构", remap_of(two, "modmap")["BTN_SIDE"]
      == {"alone": ["CTRL_L", "c"], "held": ["CTRL_L", "x"], "tap_timeout_millis": 400}, str(two))
check("有长按就必写长按阈值（不能沿用上游 1000ms 默认）",
      "tap_timeout_millis" in two, str(two))
check("只有点按时不写阈值", "tap_timeout_millis" not in one, one.replace("\n", "\\n")[:90])
check("阈值可传参", "tap_timeout_millis: 250" in
      sk.dump_yaml({"BTN_SIDE": {"tap": "C-c", "hold": "C-x"},
                    "BTN_EXTRA": {"tap": None, "hold": None, "double": None}}, "Test Mouse", 250))
mixed = cfg(BTN_SIDE={"tap": "C-c"}, BTN_EXTRA={"tap": "S-Insert", "hold": "Enter"})
check("两键分工不同 → keymap 与 modmap 并存", sections(mixed) == {"keymap", "modmap"}, str(sections(mixed)))
check("keymap 里只放没有长按的那个键", set(remap_of(mixed, "keymap")) == {"BTN_SIDE"}, str(remap_of(mixed, "keymap")))
three = cfg(BTN_SIDE={"tap": "S-Insert", "double": "Enter"})
check("双击 → keymap + experimental_map", {"keymap", "experimental_map"} <= sections(three), str(sections(three)))
exp = remap_of(three, "experimental_map")["BTN_SIDE"]
check("双击写成 double + timeout", exp == {"double": "Enter", "timeout": 200}, str(exp))
check("experimental_map 不带 device（上游不支持）", "device" not in yaml.safe_load(three)["experimental_map"][0], str(three))
mac = cfg(BTN_SIDE={"tap": "C-c, Enter"})
check("宏写成 YAML 序列而不是字符串", remap_of(mac, "keymap")["BTN_SIDE"] == ["C-c", "Enter"], str(mac))
check("屏蔽写成空序列", remap_of(cfg(BTN_SIDE={"tap": "[]"}), "keymap")["BTN_SIDE"] == [])

# ---------- 4) 读回（round-trip）----------
text = cfg(BTN_SIDE={"tap": "C-c", "hold": "C-x", "double": "Enter"}, BTN_EXTRA={"tap": "S-Insert"})
with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False, encoding="utf-8") as fh:
    fh.write(text)
    p = fh.name
old, sk.CONFIG_PATH = sk.CONFIG_PATH, p
old_be = sk.BACKEND_OVERRIDE
sk.BACKEND_OVERRIDE = sk.XREMAP_BACKEND
try:
    got, dev, ht, backend, ar = sk.read_current()
finally:
    sk.CONFIG_PATH, sk.BACKEND_OVERRIDE = old, old_be
os.unlink(p)
check("round-trip 长按阈值", ht == 400, str(ht))
check("round-trip 点按", got["BTN_SIDE"]["tap"] == "C-c", str(got))
check("round-trip 长按", got["BTN_SIDE"]["hold"] == "C-x", str(got))
check("round-trip 双击", got["BTN_SIDE"]["double"] == "Enter", str(got))
check("round-trip 另一键点按", got["BTN_EXTRA"]["tap"] == "S-Insert", str(got))
check("round-trip 设备名", dev == "Test Mouse", str(dev))
check("round-trip 未启用项为空", got["BTN_EXTRA"]["hold"] is None and got["BTN_EXTRA"]["double"] is None, str(got))

# ---------- 5) keyd 后端：生成 / 读回 / 校验 ----------
def kb(**kw):
    b = {k: {"tap": None, "hold": None, "double": None} for k, _ in sk.BUTTONS}
    for code, gestures in kw.items():
        b[code].update(gestures)
    return sk.dump_keyd(b, "3151:3022", 300, True)


KEYD_LEGAL = [
    ("点按复制", kb(BTN_SIDE={"tap": "C-c"})),
    ("点按+长按回车（一次性宏）", kb(BTN_SIDE={"tap": "C-Insert", "hold": "Enter"})),
    ("只有长按（点按留空＝noop）", kb(BTN_SIDE={"hold": "Enter"})),
    ("屏蔽该键", kb(BTN_SIDE={"tap": "[]"}, BTN_EXTRA={"tap": "C-v"})),
    ("宏序列走 macro()", kb(BTN_SIDE={"tap": "C-c, Enter"})),
    ("Super 前缀映射到 M-", kb(BTN_SIDE={"tap": "Super-L"})),
    ("AltGr 前缀 G-", kb(BTN_SIDE={"tap": "G-2"})),
]
for name, text in KEYD_LEGAL:
    check(f"keyd 合法写法不被误杀：{name}", not errs_of(text), str(errs_of(text))[:120])

check("keyd 长按用 timeout(点按, 毫秒, 动作)",
      "mouse1 = timeout(C-insert, 300, macro2(0, 0, enter))" in kb(BTN_SIDE={"tap": "C-Insert", "hold": "Enter"}),
      kb(BTN_SIDE={"tap": "C-Insert", "hold": "Enter"}).replace("\n", "\\n")[-90:])
check("keyd 自动释放关闭时不包 macro2",
      "macro2" not in sk.dump_keyd({"BTN_SIDE": {"tap": None, "hold": "Enter", "double": None},
                                    "BTN_EXTRA": {"tap": None, "hold": None, "double": None}},
                                   "3151:3022", 300, False))
check("keyd 有双击直接报错（上游没有算子）",
      _raises(lambda: sk.dump_keyd({"BTN_SIDE": {"tap": None, "hold": None, "double": "Enter"},
                                    "BTN_EXTRA": {"tap": None, "hold": None, "double": None}},
                                   "3151:3022", 300, True)))
check("keyd 没设备会拒绝盲写 ids",
      _raises(lambda: sk.dump_keyd({"BTN_SIDE": {"tap": "C-c", "hold": None, "double": None},
                                    "BTN_EXTRA": {"tap": None, "hold": None, "double": None}},
                                   "", 300, True)))
check("keyd 阈值越界在生成阶段就被拒",
      _raises(lambda: sk.dump_keyd({"BTN_SIDE": {"tap": None, "hold": "Enter", "double": None},
                                    "BTN_EXTRA": {"tap": None, "hold": None, "double": None}},
                                   "3151:3022", 9000, True))
      and _raises(lambda: sk.dump_keyd({"BTN_SIDE": {"tap": None, "hold": "Enter", "double": None},
                                        "BTN_EXTRA": {"tap": None, "hold": None, "double": None}},
                                       "3151:3022", "abc", True)))

KEYD_ILLEGAL = [
    ("阈值越界", kb(BTN_SIDE={"tap": "C-Insert", "hold": "Enter"}).replace(", 300,", ", 9000,")),
    ("阈值非数字", kb(BTN_SIDE={"tap": "C-Insert", "hold": "Enter"}).replace(", 300,", ", fast,")),
    ("乱造键名", kb(BTN_SIDE={"tap": "C-Insert", "hold": "Enter"}).replace("enter))", "totallybogus))")),
    ("错误修饰前缀", kb(BTN_SIDE={"tap": "C-Insert"}).replace("C-insert", "H-insert")),
    ("ids 用 *", "[ids]\n\n*\n\n[main]\n\nmouse1 = c-c\n"),
    ("ids 写成设备名", "[ids]\n\nYICHIP Wireless Device Mouse\n\n[main]\n\nmouse1 = c-c\n"),
    ("两个侧键都没绑", "[ids]\n\n3151:3022\n\n[main]\n\n# 空\n"),
    ("timeout 少参数", "[ids]\n\n3151:3022\n\n[main]\n\nmouse1 = timeout(c-insert, 300)\n"),
    ("裸绑定行也要走 keyd 规则（不给 YAML 式误导）",
     "mouse1 = timeout(c-insert, 300)\n"),
]
for name, text in KEYD_ILLEGAL:
    got = errs_of(text)
    check(f"keyd 拦住：{name}", bool(got), str(got)[:120])
BARE = "mouse1 = timeout(c-insert, 300)\n"
check("keyd 裸绑定行报的是 keyd 的错",
      any("keyd" in x or "ids" in x for x in errs_of(BARE)), str(errs_of(BARE))[:120])

# keyd 配置读回（与生成器互为逆运算）
rt = kb(BTN_SIDE={"tap": "C-Insert", "hold": "Enter"}, BTN_EXTRA={"tap": "S-Insert"})
dev_rt, b_rt, ms_rt, ar_rt = sk.parse_keyd(rt)
check("keyd round-trip 设备 id", dev_rt == "3151:3022", str(dev_rt))
check("keyd round-trip 点按", b_rt["BTN_SIDE"]["tap"] == "C-Insert" and b_rt["BTN_EXTRA"]["tap"] == "S-Insert", str(b_rt))
check("keyd round-trip 长按", b_rt["BTN_SIDE"]["hold"] == "Enter" and b_rt["BTN_EXTRA"]["hold"] is None, str(b_rt))
check("keyd round-trip 阈值", ms_rt == 300, str(ms_rt))
check("keyd round-trip 自动释放", ar_rt is True, str(ar_rt))
check("keyd 生成→读回→再生成 幂等",
      sk.dump_keyd(b_rt, dev_rt, ms_rt, ar_rt) == rt, "见上一条")
no_ar = sk.dump_keyd({"BTN_SIDE": {"tap": None, "hold": "Enter", "double": None},
                      "BTN_EXTRA": {"tap": None, "hold": None, "double": None}}, "3151:3022", 400, False)
check("keyd 读回能识别未开自动释放", sk.parse_keyd(no_ar)[3] is False, str(sk.parse_keyd(no_ar)))

# ---------- 6) 线上配置没写阈值时，--print 这条路径不能炸 ----------
LEGACY = """keymap:
- name: 侧键映射
  device:
    only:
    - Test Mouse
  remap:
    BTN_SIDE: C-c
"""
with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False, encoding="utf-8") as fh:
    fh.write(LEGACY)
    legacy_path = fh.name
old_cfg, old_be = sk.CONFIG_PATH, sk.BACKEND_OVERRIDE
sk.CONFIG_PATH, sk.BACKEND_OVERRIDE = legacy_path, sk.XREMAP_BACKEND
try:
    b_legacy, d_legacy, ht_legacy, be_legacy, ar_legacy = sk.read_current()
finally:
    sk.CONFIG_PATH, sk.BACKEND_OVERRIDE = old_cfg, old_be
os.unlink(legacy_path)
check("旧配置读回阈值为 None", ht_legacy is None, str(ht_legacy))
out_legacy = sk.dump_config(be_legacy, b_legacy, d_legacy, ht_legacy, ar_legacy)
check("纯点按的旧配置形状不变（不凭空塞进阈值、也不抛异常）",
      "tap_timeout_millis" not in out_legacy, out_legacy.replace("\n", "\\n")[:110])
hold_legacy = sk.dump_config(sk.XREMAP_BACKEND,
                             {"BTN_SIDE": {"tap": "C-c", "hold": "Enter", "double": None},
                              "BTN_EXTRA": {"tap": None, "hold": None, "double": None}},
                             "Test Mouse", None, True)
check("有长按时 None 阈值补成默认而不是报错",
      f"tap_timeout_millis: {sk.HOLD_TIMEOUT_DEFAULT}" in hold_legacy, hold_legacy.replace("\n", "\\n")[-90:])

# ---------- 7) keyd 分支经 read_current 的完整读回（含后端识别） ----------
KEYD_LIVE = sk.dump_keyd({"BTN_SIDE": {"tap": "C-Insert", "hold": "Enter", "double": None},
                          "BTN_EXTRA": {"tap": "S-Insert", "hold": None, "double": None}},
                         "3151:3022", 350, True)
with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False, encoding="utf-8") as fh:
    fh.write(KEYD_LIVE)
    keyd_path = fh.name
old_keyd, old_be2 = sk.KEYD_CONFIG_PATH, sk.BACKEND_OVERRIDE
sk.KEYD_CONFIG_PATH, sk.BACKEND_OVERRIDE = keyd_path, sk.KEYD_BACKEND
try:
    kb, kd, kms, kbe, kar = sk.read_current()
finally:
    sk.KEYD_CONFIG_PATH, sk.BACKEND_OVERRIDE = old_keyd, old_be2
os.unlink(keyd_path)
check("keyd 配置经 read_current 认出后端", kbe == sk.KEYD_BACKEND, str(kbe))
check("keyd 配置读回设备名（vid:pid 反查）", kd == "YICHIP Wireless Device Mouse" or kd == "3151:3022", str(kd))
check("keyd 配置读回阈值", kms == 350, str(kms))
check("keyd 配置读回自动释放", kar is True, str(kar))
check("keyd 配置读回绑定", kb["BTN_SIDE"] == {"tap": "C-Insert", "hold": "Enter", "double": None}, str(kb))

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail[:100]}]" if not ok and detail else ""))
print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
sys.exit(1 if failed else 0)
