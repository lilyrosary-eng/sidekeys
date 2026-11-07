#!/usr/bin/env python3
"""侧键映射 —— xremap 两个鼠标侧键的图形配置器。

不做实时按键捕获：改动只在按“应用”时一次性写入 /etc/xremap/config.yml 并重启 xremap。
所有跨进程操作都在后台线程里跑，结果用 idle_add 送回主循环，界面不会冻结。
"""

import os
import re
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import yaml
from gi.repository import Adw, Gio, GLib, Gtk

CONFIG_PATH = "/etc/xremap/config.yml"
STAGE_PATH = f"/tmp/sidekeys-stage-{os.getuid()}.yml"
APPLY_HELPER = "/usr/local/bin/sidekeys-apply"
UNIT = "sidekeys"
NO_DEVICE = "（没检测到鼠标设备）"

PRESETS = [
    ("复制", "C-c"),
    ("粘贴", "C-v"),
    ("剪切", "C-x"),
    # 终端里 Ctrl+C 是 SIGINT、Ctrl+V 是字面插入，所以给出 PC 传统的剪贴板键
    ("复制（终端里也能用）", "C-Insert"),
    ("粘贴（终端里也能用）", "S-Insert"),
    ("新开标签页", "C-S-T"),
    ("关闭标签页", "C-w"),
    ("浏览器后退", "A-Left"),
    ("浏览器前进", "A-Right"),
    ("刷新页面", "F5"),
    ("下一个窗口", "A-Tab"),
    ("上一个窗口", "Shift-A-Tab"),
    ("锁屏", "Super-L"),
    ("屏蔽这个侧键", "[]"),
    ("自定义…", ""),
]
CUSTOM_LABEL = PRESETS[-1][0]
TOKEN_BY_LABEL = dict(PRESETS)
BUTTONS = [("BTN_SIDE", "后侧键"), ("BTN_EXTRA", "前侧键")]
DEFAULT_REMAP = {"BTN_SIDE": "C-c", "BTN_EXTRA": "C-v"}

VALID_TOKEN = re.compile(r"^[A-Za-z0-9_.+-]+$|^\[\]$")


def label_for(token):
    for label, value in PRESETS:
        if value and value == token:
            return label
    return token or "屏蔽"


def dump_yaml(remap, device):
    block = {"name": "侧键映射"}
    if device:
        block["device"] = {"only": [device]}
    body = {}
    for code, _ in BUTTONS:
        value = remap.get(code) or "[]"
        # xremap 里“屏蔽这个键”是空序列 []，不是字符串 "[]"
        body[code] = [] if value == "[]" else value
    block["remap"] = body
    header = "# 由侧键映射工具生成。手改前先用 sidekeys.py --print 看现状。\n"
    return header + yaml.safe_dump(
        {"keymap": [block]}, allow_unicode=True, sort_keys=False, default_flow_style=False
    )


def read_current():
    remap = dict(DEFAULT_REMAP)
    device = None
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return remap, device
    for block in doc.get("keymap") or []:
        for code, _ in BUTTONS:
            if code in (block.get("remap") or {}):
                value = block["remap"][code]
                remap[code] = "[]" if value in (None, [], {}) else str(value)
        only = (block.get("device") or {}).get("only")
        if only:
            device = str(only[0])
        elif "device" in block:
            device = None
    return remap, device


def list_mice():
    names = []
    try:
        with open("/proc/bus/input/devices", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return names
    for chunk in text.split("\n\n"):
        handlers = re.search(r"H:\s+Handlers=(.*)", chunk)
        name = re.search(r'N:\s+Name="(.*)"', chunk)
        sysfs = re.search(r"S:\s+Sysfs=(.*)", chunk)
        if not handlers or not name:
            continue
        # 跳过虚拟设备：xremap 自己创建的输出设备也叫 mouse，映射它会形成回环
        if sysfs and "/devices/virtual/" in sysfs.group(1):
            continue
        if any(tok.startswith("mouse") for tok in handlers.group(1).split()) and "Touchpad" not in name.group(1):
            names.append(name.group(1))
    return names


def run_in_thread(argv, callback, timeout=180):
    def work():
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            rc, out, err = proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except FileNotFoundError:
            rc, out, err = 127, "", f"找不到 {argv[0]}"
        except subprocess.TimeoutExpired:
            rc, out, err = 124, "", "超时（polkit 弹窗可能没被确认）"
        except Exception as exc:
            rc, out, err = 1, "", str(exc)
        GLib.idle_add(callback, rc, out, err)

    threading.Thread(target=work, daemon=True).start()


def poll_service(callback):
    run_in_thread(
        ["systemctl", "show", UNIT, "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts"],
        lambda rc, out, err: callback(dict(
            line.split("=", 1) for line in out.splitlines() if "=" in line
        )),
        timeout=10,
    )


class SideKeysWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(560, 640)
        self.busy = False
        remap, device = read_current()

        self.header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title="侧键映射", subtitle="")
        self.header.set_title_widget(self.title_widget)

        self.undo_btn = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="恢复上一版配置")
        self.undo_btn.connect("clicked", lambda *_: self._dispatch(["restore"], "已恢复上一版配置"))
        self.header.pack_start(self.undo_btn)

        self.apply_btn = Gtk.Button(label="应用")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.connect("clicked", self._on_apply)
        self.header.pack_end(self.apply_btn)

        self.banner = Adw.Banner()
        start_action = Gio.SimpleAction.new("start-service", None)
        start_action.connect("activate", lambda *_: self._dispatch(["start"], "服务已启动"))
        self.add_action(start_action)
        self.banner.set_property("action-name", "win.start-service")

        rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        rows_box.set_margin_top(12)
        rows_box.set_margin_bottom(24)
        rows_box.set_margin_start(12)
        rows_box.set_margin_end(12)
        self.rows_box = rows_box

        self.rows = {}
        for code, cn in BUTTONS:
            group = Adw.PreferencesGroup(title=f"{cn}（{code}）")
            combo = Adw.ComboRow(title="映射为")
            combo.set_model(Gtk.StringList.new([label for label, _ in PRESETS]))
            combo.set_selected(self._preset_index(remap[code]))
            combo.connect("notify::selected", self._on_choice, code)
            group.add(combo)

            entry = Adw.EntryRow(title="xremap 写法，例如 C-c / A-Tab / F5 / []")
            entry.set_text(remap[code])
            entry.connect("changed", lambda *_: self._update_preview())
            group.add(entry)

            self.rows[code] = (combo, entry)
            rows_box.append(group)

        dev_group = Adw.PreferencesGroup(title="作用范围")
        self.device_model = Gtk.StringList.new([])
        self.device_row = Adw.ComboRow(
            title="鼠标设备", subtitle="只接管选中的那一个，笔记本键盘和触控板不受影响"
        )
        self.device_row.set_model(self.device_model)
        self.device_row.connect("notify::selected", lambda *_: self._update_preview())
        dev_group.add(self.device_row)
        rows_box.append(dev_group)

        adv_group = Adw.PreferencesGroup(title="将写入的 YAML", description="只读预览；点“应用”才会写入并生效")
        preview_scroll = Gtk.ScrolledWindow()
        preview_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        preview_scroll.set_propagate_natural_height(True)
        preview_scroll.set_max_content_height(240)
        self.preview_text = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
        self.preview_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        preview_scroll.set_child(self.preview_text)
        adv_group.add(preview_scroll)
        rows_box.append(adv_group)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=560)
        clamp.set_child(rows_box)
        scrolled.set_child(clamp)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.banner)
        content.append(scrolled)

        self.toast = Adw.ToastOverlay()
        self.toast.set_child(content)

        bar = Adw.ToolbarView()
        bar.add_top_bar(self.header)
        bar.set_content(self.toast)
        self.set_content(bar)

        self._populate_devices(device)
        for code, _ in BUTTONS:
            self._sync_custom_visibility(code)
        self._update_preview()
        self._refresh_status()
        GLib.timeout_add_seconds(5, self._refresh_status)

    def _preset_index(self, token):
        for idx, (label, value) in enumerate(PRESETS):
            if value and value == token:
                return idx
        return len(PRESETS) - 1

    def _populate_devices(self, current):
        names = list_mice()
        if current and current not in names:
            names.insert(0, current)
        if not names:
            names = [NO_DEVICE]
        for name in names:
            self.device_model.append(name)
        target = current or names[0]
        self.device_row.set_selected(names.index(target) if target in names else 0)

    def _selected_device(self):
        idx = self.device_row.get_selected()
        if idx < 0 or idx >= self.device_model.get_n_items():
            return None
        name = self.device_model.get_string(idx)
        return None if name == NO_DEVICE else name

    def _sync_custom_visibility(self, code):
        combo, entry = self.rows[code]
        is_custom = combo.get_selected() == len(PRESETS) - 1
        entry.set_visible(is_custom)
        if not is_custom:
            entry.set_text(TOKEN_BY_LABEL[combo.get_selected_item().get_string()])

    def _on_choice(self, combo, _param, code):
        self._sync_custom_visibility(code)
        self._update_preview()

    def current_remap(self):
        remap = {}
        for code, _ in BUTTONS:
            combo, entry = self.rows[code]
            if combo.get_selected() == len(PRESETS) - 1:
                token = entry.get_text().strip()
            else:
                token = TOKEN_BY_LABEL[combo.get_selected_item().get_string()]
            remap[code] = token or "[]"
        return remap

    def _update_preview(self):
        remap = self.current_remap()
        self.preview_text.get_buffer().set_text(dump_yaml(remap, self._selected_device()))
        self.title_widget.set_subtitle(
            f"后侧键 = {label_for(remap['BTN_SIDE'])} ／ 前侧键 = {label_for(remap['BTN_EXTRA'])}"
        )

    def _refresh_status(self):
        poll_service(self._on_status)
        return True

    def _on_status(self, props):
        state = props.get("ActiveState", "unknown")
        if state == "active":
            self.banner.set_revealed(False)
        else:
            self.banner.set_title(f"xremap 未在运行（{state}，历史重启 {props.get('NRestarts', '?')} 次）")
            self.banner.set_button_label("启动服务")
            self.banner.set_revealed(True)

    def _on_apply(self, _btn):
        remap = self.current_remap()
        device = self._selected_device()
        bad = [code for code, token in remap.items() if not VALID_TOKEN.match(token)]
        if bad:
            self._toast(f"{', '.join(bad)} 的写法里有不被接受的字符", error=True)
            return
        if not device:
            self._toast("没有可选的鼠标设备。本工具刻意只接管单个设备，不去抓全部键盘", error=True)
            return
        payload = dump_yaml(remap, device)
        if "BTN_SIDE" not in payload or not payload.strip():
            self._toast("生成的配置里没有侧键映射，已中止", error=True)
            return
        try:
            with open(STAGE_PATH, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.chmod(STAGE_PATH, 0o600)
            if os.path.getsize(STAGE_PATH) != len(payload.encode("utf-8")):
                raise OSError("暂存文件写入不完整")
        except OSError as exc:
            self._toast(f"无法写暂存文件：{exc}", error=True)
            return
        self._dispatch(["apply", STAGE_PATH, device], "已生效")

    def _dispatch(self, args, ok_message):
        if self.busy:
            return
        self.busy = True
        self.apply_btn.set_sensitive(False)
        self.undo_btn.set_sensitive(False)
        run_in_thread(["pkexec", APPLY_HELPER] + args, lambda rc, out, err: self._done(rc, out, err, ok_message))

    def _done(self, rc, out, err, ok_message):
        self.busy = False
        self.apply_btn.set_sensitive(True)
        self.undo_btn.set_sensitive(True)
        if rc == 0:
            self._toast(out or ok_message)
        elif rc == 126 or "Cancelled" in err or "not authorized" in err.lower():
            self._toast("已取消授权，未做任何改动")
        else:
            self._toast(f"应用失败：{err or out or f'退出码 {rc}'}", error=True)
        self._refresh_status()

    def _toast(self, message, error=False):
        toast = Adw.Toast.new(message)
        toast.set_timeout(7 if error else 4)
        toast.set_priority(Adw.ToastPriority.HIGH if error else Adw.ToastPriority.NORMAL)
        self.toast.add_toast(toast)


def cli_mode(argv):
    remap, device = read_current()
    if argv[0] == "--print":
        sys.stdout.write(dump_yaml(remap, device))
        return 0
    if argv[0] == "--mice":
        print("\n".join(list_mice()))
        return 0
    if argv[0] == "--render" and len(argv) >= 5:
        sys.stdout.write(dump_yaml({"BTN_SIDE": argv[2], "BTN_EXTRA": argv[4]}, device))
        return 0
    print("用法: sidekeys.py [--print | --mice | --render SIDE C-c EXTRA C-v]", file=sys.stderr)
    return 2


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--print", "--mice", "--render"):
        return cli_mode(sys.argv[1:])
    app = Adw.Application(application_id="com.andeyunhui.sidekeys")

    def activate(application):
        window = SideKeysWindow(application=application)
        window.present()

    app.connect("activate", activate)
    return app.run(None)


if __name__ == "__main__":
    Adw.init()
    sys.exit(main())
