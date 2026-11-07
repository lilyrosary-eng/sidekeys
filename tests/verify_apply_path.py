#!/usr/bin/env python3
"""只读验证 _on_apply 的前置校验与参数传递（不触发 pkexec，不改线上配置）。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import yaml

import sidekeys as sk
from gi.repository import Adw, GLib

Adw.init()
out = {}
app = Adw.Application(application_id="verify.apply.path")


def activate(a):
    win = sk.SideKeysWindow(application=a)
    win.present()
    toasts = []
    win._toast = lambda msg, error=False: toasts.append((msg, error))
    win._dispatch = lambda args, okmsg: out.update(args=args, ok=okmsg)

    ch, _e, lh = win.rows[("BTN_SIDE", "hold")]
    ch.set_selected(lh.index("剪切"))
    cd, _e2, ld = win.rows[("BTN_SIDE", "double")]
    cd.set_selected(ld.index("回车 / 执行"))
    win._on_apply(win.apply_btn)

    out["toasts"] = toasts
    stage = open(sk.STAGE_PATH, encoding="utf-8").read()
    doc = yaml.safe_load(stage)
    out["sections"] = set(doc)
    out["modmap_side"] = (doc.get("modmap") or [{}])[0].get("remap", {}).get("BTN_SIDE")
    out["expmap_side"] = (doc.get("experimental_map") or [{}])[0].get("remap", {}).get("BTN_SIDE")
    out["expmap_has_device"] = "device" in (doc.get("experimental_map") or [{}])[0]
    a.quit()


app.connect("activate", activate)
app.run(None)

ok = True
def expect(name, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   [{extra}]" if not cond else ""))

expect("前置校验未拦下合法配置（无 error toast）", not [t for t in out.get("toasts", []) if t[1]], str(out.get("toasts")))
expect("确实走到了 dispatch", "args" in out, str(out))
expect("dispatch 参数含设备名", out.get("args", [None, None, None])[2] == sk.read_current()[1], str(out.get("args")))
expect("三层区块齐备", out.get("sections") == {"keymap", "modmap", "experimental_map"} or out.get("sections") == {"modmap", "experimental_map"},
       str(out.get("sections")))
expect("modmap 有 alone/held", isinstance(out.get("modmap_side"), dict) and "held" in (out.get("modmap_side") or {}), str(out.get("modmap_side")))
expect("experimental_map 有 double/timeout", isinstance(out.get("expmap_side"), dict) and (out.get("expmap_side") or {}).get("double") == "Enter", str(out.get("expmap_side")))
expect("experimental_map 不带 device", out.get("expmap_has_device") is False, str(out.get("expmap_has_device")))
print("\n结论:", "✓ 通过" if ok else "✗ 见上")
sys.exit(0 if ok else 1)
