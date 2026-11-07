#!/usr/bin/env python3
"""只读验证 _on_apply 的前置校验与参数传递（不触发 pkexec，不改线上配置）。

两个后端各走一遍：确认界面 → 生成 → 前置校验 → dispatch 参数这条链在
xremap（YAML + 双击）和 keyd（INI + 无双击）下都成立。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import yaml

import sidekeys as sk
from gi.repository import Adw, GLib

Adw.init()
results = []


def expect(name, cond, extra=""):
    results.append((name, bool(cond), extra))


app = Adw.Application(application_id="verify.apply.path")


def stage_text():
    with open(sk.STAGE_PATH, encoding="utf-8") as fh:
        return fh.read()


def activate(a):
    try:
        run_case(a)
    except Exception as exc:  # 不让异常把主循环留在原地，否则“失败”看起来像“卡死”
        results.append(("用例执行中抛异常", False, f"{type(exc).__name__}: {exc}"))
        a.quit()


def run_case(a):
    win = sk.SideKeysWindow(application=a)
    win.present()
    toasts = []
    win._toast = lambda msg, error=False: toasts.append((msg, error))
    calls = []
    win._dispatch = lambda args, okmsg: calls.append(args)

    # ---------- A) xremap 后端：点按 + 长按 + 双击三层都要在 ----------
    labels = [name for name, _l in sk.BACKENDS]
    win.backend_row.set_selected(labels.index(sk.XREMAP_BACKEND))
    ch, _e, lh = win.rows[("BTN_SIDE", "hold")]
    ch.set_selected(lh.index("剪切"))
    cd, _e2, ld = win.rows[("BTN_SIDE", "double")]
    cd.set_selected(ld.index("回车 / 执行"))
    before = len(calls)
    win._on_apply(win.apply_btn)
    expect("xremap：前置校验没拦下合法配置",
           not [t for t in toasts if t[1]], str([t for t in toasts if t[1]]))
    expect("xremap：确实走到了 dispatch", len(calls) == before + 1, str(calls))
    args = calls[-1] if calls else [None, None, None, None]
    expect("xremap：dispatch 参数含设备名", args[2] == sk.read_current()[1], str(args))
    expect("xremap：dispatch 参数含后端名", args[3] == sk.XREMAP_BACKEND, str(args))
    doc = yaml.safe_load(stage_text())
    expect("xremap：三层区块齐备",
           set(doc) == {"keymap", "modmap", "experimental_map"} or set(doc) == {"modmap", "experimental_map"},
           str(set(doc)))
    mod = (doc.get("modmap") or [{}])[0].get("remap", {}).get("BTN_SIDE") or {}
    expect("xremap：modmap 有 alone/held/tap_timeout_millis",
           {"alone", "held", "tap_timeout_millis"} <= set(mod), str(mod))
    exp = (doc.get("experimental_map") or [{}])[0]
    expect("xremap：experimental_map 有 double 且不带 device",
           (exp.get("remap", {}).get("BTN_SIDE") or {}).get("double") == "Enter" and "device" not in exp,
           str(exp))

    # ---------- B) keyd 后端：INI、无双击、自动释放包 macro2 ----------
    # 线上 root 助手此刻可能还是旧版（不认 keyd）。守卫本身另有断言，
    # 这里先把能力放开，免得把“能力检查”混进“链路检查”。
    sk.helper_supports_keyd = lambda: True
    win.backend_row.set_selected(labels.index(sk.KEYD_BACKEND))
    expect("keyd：双击那一栏被隐藏", all(not c.get_visible() for _code, c, _e in win.double_rows),
           str([c.get_visible() for _code, c, _e in win.double_rows]))
    expect("keyd：自动释放开关出现", win.auto_release_row.get_visible())
    expect("keyd：current_bindings 丢掉了双击",
           all(not b.get("double") for b in win.current_bindings().values()),
           str(win.current_bindings()))
    bh, _be, blh = win.rows[("BTN_SIDE", "hold")]
    bh.set_selected(blh.index("回车 / 执行"))
    win.hold_adjustment.set_value(350)
    win.auto_release_row.set_active(True)
    before = len(calls)
    seen = len(toasts)
    win._on_apply(win.apply_btn)
    expect("keyd：前置校验没拦下自己生成的配置",
           not [t for t in toasts[seen:] if t[1]], str([t for t in toasts[seen:] if t[1]]))
    expect("keyd：确实走到了 dispatch", len(calls) == before + 1, str(calls[-3:]))
    kargs = calls[-1]
    expect("keyd：dispatch 参数含后端名", kargs[3] == sk.KEYD_BACKEND, str(kargs))
    text = stage_text()
    expect("keyd：写出的是 INI（有 [ids] 与 [main]）", "[ids]" in text and "[main]" in text, text[:80])
    expect("keyd：[ids] 是解析出来的 vid:pid",
           sk.device_id_for(sk.read_current()[1]).lower() in text.lower(), text[:120])
    expect("keyd：长按被包成一次性宏（macro2(0, 0, enter)）",
           "timeout(" in text and "macro2(0, 0, enter)" in text, text)
    expect("keyd：阈值写进 timeout 的第二个参数", ", 350," in text, text)
    expect("keyd：配置里没有任何 BTN_* / experimental_map 残留",
           "BTN_" not in text and "experimental_map" not in text, text)
    expect("keyd：GUI 侧校验器接受这份 INI", not (win.validate_fn or (lambda p: []))(sk.STAGE_PATH),
           str((win.validate_fn or (lambda p: []))(sk.STAGE_PATH)))

    # 关掉自动释放应当退回裸键名（按住会重复）
    win.auto_release_row.set_active(False)
    seen = len(toasts)
    win._on_apply(win.apply_btn)
    expect("keyd：关闭自动释放也通过前置校验", not [t for t in toasts[seen:] if t[1]],
           str([t for t in toasts[seen:] if t[1]]))
    off = stage_text()
    expect("keyd：关闭自动释放后不再包 macro2", "macro2" not in off and "enter)" in off, off[-160:])

    # 守卫：旧助手必须被明确拒绝，而不是把 INI 当 YAML 写下去
    sk.helper_supports_keyd = lambda: False
    win.backend_row.set_selected(labels.index(sk.KEYD_BACKEND))
    seen = len(toasts)
    before = len(calls)
    win._on_apply(win.apply_btn)
    expect("旧 root 助手会被守卫拦下", len(calls) == before, str(calls[-1:]))
    expect("拦截时给的是 install.sh 的指引",
           any("install.sh" in t[0] for t in toasts[seen:]), str([t[0] for t in toasts[seen:]]))
    sk.helper_supports_keyd = lambda: True
    win.backend_row.set_selected(labels.index(sk.XREMAP_BACKEND))
    a.quit()


# 路径漂移检查：deb 与 install.sh 都按规范路径找助手，顺序不能反。
results.append(("助手候选首位是规范路径",
                sk.APPLY_HELPER_CANDIDATES[0] == "/usr/lib/sidekeys/sidekeys-apply",
                str(sk.APPLY_HELPER_CANDIDATES)))
results.append(("校验器候选含规范路径",
                "/usr/lib/sidekeys/sidekeys_validate.py" in sk.VALIDATOR_PATHS,
                str(sk.VALIDATOR_PATHS)))
results.append(("apply_helper 只会在真实存在时选中它",
                sk.apply_helper() in [c for c in sk.APPLY_HELPER_CANDIDATES if os.path.isfile(c)]
                or sk.apply_helper() == sk.APPLY_HELPER_CANDIDATES[0],
                sk.apply_helper()))

app.connect("activate", activate)
app.run(None)

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{str(detail)[:120]}]" if not ok and detail else ""))
print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
sys.exit(1 if failed else 0)
