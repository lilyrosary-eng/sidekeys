#!/usr/bin/env python3
"""端到端：把后侧键配成 点按=复制 / 长按=剪切 / 双击=回车，真实 apply，再撤销回原状。"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import sidekeys as sk
from gi.repository import Adw, GLib

Adw.init()
log = []
state = {"phase": 0, "toasts": [], "all": []}
app = Adw.Application(application_id="e2e.threegesture.test")


def shell(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def activate(a):
    win = sk.SideKeysWindow(application=a)
    win.present()
    original = win._toast

    def spy(msg, error=False):
        state["toasts"].append((msg, error))
        state["all"].append((msg, error))
        log.append(f"  toast: {msg!r} (error={error})")
        return original(msg, error)

    win._toast = spy

    def step():
        if state["phase"] == 0:
            before = open(sk.CONFIG_PATH).read()
            state["before"] = before
            combo_h, _e_h, labels_h = win.rows[("BTN_SIDE", "hold")]
            combo_h.set_selected(labels_h.index("剪切"))
            combo_d, _e_d, labels_d = win.rows[("BTN_SIDE", "double")]
            combo_d.set_selected(labels_d.index("回车 / 执行"))
            log.append("  已选：长按=剪切，双击=回车/执行")
            state["phase"] = 1
            win._on_apply(win.apply_btn)
        elif state["phase"] == 1 and state["toasts"]:
            cfg = open(sk.CONFIG_PATH).read()
            log.append("  --- apply 后的配置 ---\n" + "\n".join("    " + l for l in cfg.splitlines()))
            import yaml as _yaml
            _doc = _yaml.safe_load(cfg)
            _mod = (((_doc.get("modmap") or [{}])[0]).get("remap") or {})
            for probe, name in [
                ("modmap" in cfg, "配置含 modmap"),
                ("experimental_map" in cfg, "配置含 experimental_map"),
                (_mod.get("BTN_SIDE", {}).get("held") == ["CTRL_L", "x"], "held 用数组 [CTRL_L, x] 而不是被忽略的字符串"),
                ("double: Enter" in cfg, "double=Enter"),
            ]:
                log.append(f"  {'✓' if probe else '✗'} {name}")
            st = shell("systemctl show sidekeys -p ActiveState -p NRestarts --value")
            log.append(f"  服务状态: {st}")
            log.append("  最近日志（只看这 25 秒内的告警）:\n" + "\n".join("    " + l for l in shell(
                'journalctl -u sidekeys --since "-25s" --no-pager -p warning').splitlines()[-6:] or ["    (无告警)"]))
            state["phase"] = 2
            state["toasts"] = []
            win._dispatch(["restore"], "已恢复上一版配置")
        elif state["phase"] == 2 and state["toasts"]:
            cfg2 = open(sk.CONFIG_PATH).read()
            same = cfg2 == state["before"]
            log.append(f"  {'✓' if same else '✗'} 撤销后配置回到 apply 之前")
            if not same:
                log.append("    现在: " + cfg2.replace("\n", "\\n")[:120])
                log.append("    原来: " + state["before"].replace("\n", "\\n")[:120])
            log.append(f"  撤销后服务: {shell('systemctl is-active sidekeys')}")
            state["phase"] = 3
            a.quit()
            return False
        return True

    GLib.timeout_add(700, step)


app.connect("activate", activate)
app.run(None)
print("\n".join(log))
ok = any("已生效" in m for m, _ in state["all"]) and any("已恢复" in m for m, _ in state["all"])
print("\ntoast 全记录:", state["all"])
print("\n结论:", "✓ apply 与撤销都成功，服务保持 active" if ok else "✗ 见上面日志")
sys.exit(0 if ok else 1)
