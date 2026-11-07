#!/usr/bin/env python3
"""遍历侧键映射工具的控件树，断言关键行为。需要已有 GUI 会话（X11/Wayland 均可）。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import sidekeys as sk  # noqa: E402
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def walk(widget, depth=0):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child, depth + 1)
        child = child.get_next_sibling()


def buf_text(view):
    buf = view.get_buffer()
    start, end = buf.get_start_iter(), buf.get_end_iter()
    try:
        return buf.get_text(start, end)
    except TypeError:
        return buf.get_text(start, end, False)


remap, device = sk.read_current()
app = Adw.Application(application_id="com.andeyunhui.sidekeys.test")
win = None


def on_activate(application):
    global win
    win = sk.SideKeysWindow(application=application)
    win.present()
    GLib.timeout_add(400, inspect_)


def inspect_():
    widgets = list(walk(win))
    types = [type(w).__name__ for w in widgets]

    check("窗口创建且进入渲染", win.get_realized() or win.get_mapped(), f"控件数={len(widgets)}")
    combos = [w for w in widgets if isinstance(w, Adw.ComboRow)]
    check("两个侧键下拉 + 一个设备下拉", len(combos) == 3, f"实际={len(combos)}")

    titles = [w.get_title() for w in widgets if isinstance(w, Adw.PreferencesGroup)]
    check("分组标题齐全", all(any(code in t for t in titles) for code, _ in sk.BUTTONS), str(titles))

    button_rows = [c for c in combos if c.get_title() == "映射为"]
    check(f"侧键下拉各有 {len(sk.PRESETS)} 个预设", all(c.get_model().get_n_items() == len(sk.PRESETS) for c in button_rows))
    check("含终端里也能用的剪贴板预设", {"C-Insert", "S-Insert"} <= {v for _, v in sk.PRESETS})
    expected = {code: sk.PRESETS.index(next(p for p in sk.PRESETS if p[1] == remap[code])) for code, _ in sk.BUTTONS}
    got = {code: c.get_selected() for code, c in zip([c for c, _ in sk.BUTTONS], button_rows)}
    check("下拉初始选项 = 线上配置", got == expected, f"got={got} expect={expected}")

    dev_row = next(c for c in combos if c.get_title() == "鼠标设备")
    names = [dev_row.get_model().get_string(i) for i in range(dev_row.get_model().get_n_items())]
    check("设备列表非空", len(names) > 0, str(names))
    check("设备列表已过滤虚拟设备 xremap", not any(n == "xremap" for n in names), str(names))
    sel = dev_row.get_selected_item().get_string()
    check("线上配置里的设备出现在列表中", device is None or device in names, f"device={device} names={names}")
    check("默认选中项合法", sel in names, sel)

    preview = next(w for w in widgets if isinstance(w, Gtk.TextView))
    text = buf_text(preview)
    check("预览 YAML 与生成器一致", text == sk.dump_yaml(remap, device), text.replace("\n", "\\n")[:80])
    check("预览含侧键码", "BTN_SIDE" in text and "BTN_EXTRA" in text)

    title = next(w for w in widgets if isinstance(w, Adw.WindowTitle))
    check("标题栏副标题显示当前映射", "复制" in title.get_subtitle(), title.get_subtitle())

    # 交互：把后侧键改成“自定义…”应显示输入行，且预览随之变化
    side_combo, side_entry = win.rows["BTN_SIDE"]
    check("预设态下自定义输入行隐藏", not side_entry.get_visible())
    side_combo.set_selected(len(sk.PRESETS) - 1)
    check("选自定义后输入行可见", side_entry.get_visible())
    side_entry.set_text("A-Tab")
    after = buf_text(preview)
    check("自定义值进入预览", "BTN_SIDE: A-Tab" in after, after.splitlines()[-1] if after else "")

    # 屏蔽写法应生成空序列
    side_combo.set_selected(next(i for i, p in enumerate(sk.PRESETS) if p[1] == "[]"))
    blocked = buf_text(preview)
    check("屏蔽写法生成空序列 []", "BTN_SIDE: []" in blocked, blocked.splitlines()[-1])

    app.quit()
    return False


app.connect("activate", on_activate)
app.run(None)

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if not ok and detail else ""))
print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
sys.exit(1 if failed else 0)
