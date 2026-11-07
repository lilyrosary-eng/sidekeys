#!/usr/bin/env python3
"""遍历侧键映射工具的控件树，断言界面结构与联动。需要已有 GUI 会话（X11/Wayland 均可）。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import sidekeys as sk  # noqa: E402
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child)
        child = child.get_next_sibling()


def buf_text(view):
    buf = view.get_buffer()
    start, end = buf.get_start_iter(), buf.get_end_iter()
    try:
        return buf.get_text(start, end)
    except TypeError:
        return buf.get_text(start, end, False)


bindings, device, hold_timeout = sk.read_current()
hold_ms = hold_timeout if isinstance(hold_timeout, int) else sk.HOLD_TIMEOUT_DEFAULT
app = Adw.Application(application_id="com.andeyunhui.sidekeys.test")
win = None


def on_activate(application):
    global win
    win = sk.SideKeysWindow(application=application)
    win.present()
    GLib.timeout_add(500, inspect_)


def inspect_():
    widgets = list(win and walk(win) or [])
    combos = [w for w in widgets if isinstance(w, Adw.ComboRow)]
    entries = [w for w in widgets if isinstance(w, Adw.EntryRow)]

    check("窗口创建且进入渲染", win.get_mapped() or win.get_realized(), f"控件数={len(widgets)}")
    check("每个侧键三个手势 + 设备 = 7 个下拉", len(combos) == len(sk.BUTTONS) * len(sk.GESTURES) + 1, f"实际={len(combos)}")
    check("每个绑定点一个自定义输入行", len(entries) == len(sk.BUTTONS) * len(sk.GESTURES), f"实际={len(entries)}")

    titles = [w.get_title() for w in widgets if isinstance(w, Adw.PreferencesGroup)]
    check("两个侧键分组存在", all(any(code in t for t in titles) for code, _ in sk.BUTTONS), str(titles))
    check("手势行标题齐全", {g[1] for g in sk.GESTURES} <= {c.get_title() for c in combos})

    for code, cn in sk.BUTTONS:
        for kind, gn in sk.GESTURES:
            combo, entry, labels = win.rows[(code, kind)]
            label = combo.get_selected_item().get_string()
            want = bindings[code].get(kind)
            expect = sk.NONE_LABEL if (kind in ("hold", "double") and not want) else (want or sk.CUSTOM_LABEL)
            check(f"{cn}/{gn} 初始选项等于线上配置", label == expect or (want and label == sk.CUSTOM_LABEL),
                  f"label={label} expect={expect}")
            check(f"{cn}/{gn} 长按与双击默认可不启用", (kind == "tap") or (sk.NONE_LABEL in labels))

    preview = next(w for w in widgets if isinstance(w, Gtk.TextView))
    text = buf_text(preview)
    ph = win.preview_scroll.get_height()
    _m, nh, _p, _n = win.preview_scroll.measure(Gtk.Orientation.VERTICAL, -1)
    check(f"预览框实际高度不被压扁（实测 {ph}px，自然高度 {nh}px）", ph >= 160, f"ph={ph} nh={nh}")
    check("预览与生成器一致", text == sk.dump_yaml(bindings, device, hold_ms), text.replace("\n", "\\n")[:70])
    check("预览含侧键码", "BTN_SIDE" in text and "BTN_EXTRA" in text)

    # 长按阈值控件：初始值等于线上配置（没有则用工具默认值），改动要立刻反映到预览
    check("长按阈值初值等于线上配置", int(win.hold_row.get_value()) == hold_ms,
          f"行={win.hold_row.get_value()} 线上={hold_ms}")
    check("阈值行出现在长按判定分组里", any("长按" in t for t in titles), str(titles))
    check("长按阈值可调范围为 100~3000",
          (win.hold_adjustment.get_lower(), win.hold_adjustment.get_upper()) == (100, 3000),
          f"{win.hold_adjustment.get_lower()}~{win.hold_adjustment.get_upper()}")
    hcombo, hentry, hlabels = win.rows[("BTN_EXTRA", "hold")]
    saved = hcombo.get_selected()
    hcombo.set_selected(hlabels.index("回车 / 执行"))
    win.hold_adjustment.set_value(250)
    with_timeout = buf_text(preview)
    check("有长按时预览写入 tap_timeout_millis", "tap_timeout_millis: 250" in with_timeout,
          with_timeout.replace("\n", "\\n")[-90:])
    hcombo.set_selected(saved)
    win.hold_adjustment.set_value(hold_ms)

    title = next(w for w in widgets if isinstance(w, Adw.WindowTitle))
    check("标题栏副标题显示当前绑定", "点按" in title.get_subtitle(), title.get_subtitle())

    # 交互：后侧键设双击=回车 → 预览应出现 experimental_map，且不写 device
    combo, entry, _labels = win.rows[("BTN_SIDE", "double")]
    combo.set_selected(_labels.index("回车 / 执行"))
    after = buf_text(preview)
    check("选双击后预览出现 experimental_map", "experimental_map" in after, after.replace("\n", "\\n")[:90])
    check("experimental_map 块不含 device", "experimental_map" in after and after.split("experimental_map")[1].count("device") == 0)

    # 交互：长按=剪切 → 该键改走 modmap
    hcombo, hentry, hlabels = win.rows[("BTN_SIDE", "hold")]
    hcombo.set_selected(hlabels.index("剪切"))
    after2 = buf_text(preview)
    import yaml
    mod_remap = ((yaml.safe_load(after2).get("modmap") or [{}])[0]).get("remap", {})
    check("选长按后 modmap 用数组形式表达组合（不是被静默忽略的字符串）",
          mod_remap.get("BTN_SIDE", {}).get("held") == ["CTRL_L", "x"], str(mod_remap.get("BTN_SIDE")))

    # 交互：自定义 → 输入行可见且进入预览；非法键名被校验器拦住
    tcombo, tentry, tlabels = win.rows[("BTN_EXTRA", "tap")]
    xh, _xhe, xl = win.rows[("BTN_EXTRA", "hold")]
    xh.set_selected(xl.index(sk.NONE_LABEL))
    xd, _xde, xdl = win.rows[("BTN_EXTRA", "double")]
    xd.set_selected(xdl.index(sk.NONE_LABEL))
    tcombo.set_selected(tlabels.index(sk.CUSTOM_LABEL))
    check("选自定义后输入行可见", tentry.get_visible())
    tentry.set_text("Return")
    import tempfile
    import sidekeys_validate as sv
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False, encoding="utf-8") as fh:
        fh.write(buf_text(preview))
        tp = fh.name
    errs = sv.validate(tp)
    os.unlink(tp)
    check("校验器拦下界面上的 Return", any("Enter" in x for x in errs), str(errs)[:120])
    tentry.set_text("Enter")
    after3 = buf_text(preview)
    check("自定义值进入 keymap", "BTN_EXTRA: Enter" in after3, after3.replace("\n", "\\n")[:140])

    # 回归：旧窗口跑旧代码时，点应用必须明确拒绝并给出出口，而不是静默无反应
    dispatched = []
    real_dispatch = win._dispatch
    win._dispatch = lambda *a, **k: dispatched.append(a)
    original_fp = sk.self_fingerprint
    sk.self_fingerprint = lambda: "0" * 64
    win._check_stale()
    check("代码更新后横幅亮起", win.stale_banner.get_revealed())
    win._on_apply(None)
    check("旧窗口点应用不会静默提交", dispatched == [], str(dispatched))
    sk.self_fingerprint = original_fp
    win.fingerprint = original_fp()
    win._check_stale()
    check("指纹一致时横幅收起", not win.stale_banner.get_revealed())
    win._dispatch = real_dispatch

    # 回归：窗口被压矮时，预览框不能被挤扁（这就是线上“框非常矮”的那个 bug）
    win.set_size_request(-1, 480)

    def check_small_window():
        h2 = win.preview_scroll.get_height()
        check(f"窗口压到 480px 高时预览框仍 >=160px（实测 {h2}px）", h2 >= 160, f"h={h2}")
        app.quit()
        return False

    GLib.timeout_add(400, check_small_window)


app.connect("activate", on_activate)
app.run(None)

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{str(detail)[:110]}]" if not ok and detail else ""))
print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
sys.exit(1 if failed else 0)
