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
try:
    got, dev, ht = sk.read_current()
finally:
    sk.CONFIG_PATH = old
os.unlink(p)
check("round-trip 长按阈值", ht == 400, str(ht))
check("round-trip 点按", got["BTN_SIDE"]["tap"] == "C-c", str(got))
check("round-trip 长按", got["BTN_SIDE"]["hold"] == "C-x", str(got))
check("round-trip 双击", got["BTN_SIDE"]["double"] == "Enter", str(got))
check("round-trip 另一键点按", got["BTN_EXTRA"]["tap"] == "S-Insert", str(got))
check("round-trip 设备名", dev == "Test Mouse", str(dev))
check("round-trip 未启用项为空", got["BTN_EXTRA"]["hold"] is None and got["BTN_EXTRA"]["double"] is None, str(got))

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail[:100]}]" if not ok and detail else ""))
print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
sys.exit(1 if failed else 0)
