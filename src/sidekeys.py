#!/usr/bin/env python3
"""侧键映射 —— xremap 两个鼠标侧键的图形配置器。

每个侧键可以绑三个手势：点按 / 长按 / 双击。
不做实时按键捕获：改动只在按“应用”时一次性写入配置并重启服务。
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
VALIDATOR_PATHS = ("/usr/local/lib/sidekeys/sidekeys_validate.py", os.path.join(os.path.dirname(os.path.abspath(__file__)), "sidekeys_validate.py"))
UNIT = "sidekeys"
NO_DEVICE = "（没检测到鼠标设备）"
NONE_LABEL = "（不启用）"
DOUBLE_TIMEOUT = 200

# 下拉里的预设：显示名 -> xremap 写法
PRESETS = [
    ("复制", "C-c"),
    ("粘贴", "C-v"),
    ("剪切", "C-x"),
    ("复制（仅部分终端绑定）", "C-Insert"),
    ("粘贴（仅部分终端绑定）", "S-Insert"),
    ("回车 / 执行", "Enter"),
    ("新开标签页", "C-S-T"),
    ("关闭标签页", "C-w"),
    ("浏览器后退", "A-Left"),
    ("浏览器前进", "A-Right"),
    ("刷新页面", "F5"),
    ("下一个窗口", "A-Tab"),
    ("上一个窗口", "Shift-A-Tab"),
    ("锁屏", "Super-L"),
    ("屏蔽这个键", "[]"),
    ("自定义…", ""),
]
CUSTOM_LABEL = PRESETS[-1][0]
TOKEN_BY_LABEL = dict(PRESETS)
LABEL_BY_TOKEN = {v: k for k, v in PRESETS if v}
BUTTONS = [("BTN_SIDE", "后侧键"), ("BTN_EXTRA", "前侧键")]
GESTURES = [("tap", "点按"), ("hold", "长按"), ("double", "双击")]
DEFAULT_BINDINGS = {
    "BTN_SIDE": {"tap": "C-c", "hold": None, "double": None},
    "BTN_EXTRA": {"tap": "C-v", "hold": None, "double": None},
}
VALID_TOKEN = re.compile(r"^[A-Za-z0-9_.+-]+(\s*,\s*[A-Za-z0-9_.+-]+)*$|^\[\]$")


def _val(token):
    """把界面上的写法转成 xremap 的值。

    “屏蔽”是空序列 []，不是字符串 "[]"；逗号分隔表示宏序列，必须转成 YAML 列表。
    """
    if token is None or token == "":
        return None
    if token == "[]":
        return []
    parts = [p.strip() for p in token.split(",") if p.strip()]
    if len(parts) > 1:
        return parts
    return token


def dump_yaml(bindings, device):
    keymap_remap = {}
    modmap_remap = {}
    expmap_remap = {}
    for code, _ in BUTTONS:
        b = bindings[code]
        tap, hold, dbl = b.get("tap"), b.get("hold"), b.get("double")
        if hold:
            # 有长按时，点按走 modmap 的 alone，避免同一键在两层各说一遍
            modmap_remap[code] = {"alone": _val(tap) or [], "held": _val(hold)}
        elif tap is not None:
            keymap_remap[code] = _val(tap) if tap else []
        if dbl:
            expmap_remap[code] = {"double": _val(dbl), "timeout": DOUBLE_TIMEOUT}

    doc = {}
    if keymap_remap:
        block = {"name": "侧键映射"}
        if device:
            block["device"] = {"only": [device]}
        block["remap"] = keymap_remap
        doc["keymap"] = [block]
    if modmap_remap:
        block = {"name": "长按行为"}
        if device:
            block["device"] = {"only": [device]}
        block["remap"] = modmap_remap
        doc["modmap"] = [block]
    if expmap_remap:
        # experimental_map 不支持 device 匹配，靠服务的 --device 限定范围
        doc["experimental_map"] = [{"name": "双击行为", "remap": expmap_remap}]
    header = "# 由侧键映射工具生成。手改前先用 sidekeys --print 看现状。\n"
    return header + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False)


def read_current():
    bindings = {k: dict(v) for k, v in DEFAULT_BINDINGS.items()}
    device = None
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return bindings, device

    def token_of(value):
        if value is None or value == []:
            return "[]" if value == [] else None
        if isinstance(value, list):
            return ", ".join(str(v) for v in value) or "[]"
        return str(value)

    for block in doc.get("keymap") or []:
        for code, target in (block.get("remap") or {}).items():
            if code in bindings:
                bindings[code]["tap"] = token_of(target)
        only = (block.get("device") or {}).get("only")
        if only:
            device = str(only[0])
    for block in doc.get("modmap") or []:
        for code, target in (block.get("remap") or {}).items():
            if code not in bindings or not isinstance(target, dict):
                continue
            if "alone" in target:
                bindings[code]["tap"] = token_of(target["alone"])
            if "held" in target:
                bindings[code]["hold"] = token_of(target["held"])
        only = (block.get("device") or {}).get("only")
        if only and not device:
            device = str(only[0])
    for block in doc.get("experimental_map") or []:
        for code, target in (block.get("remap") or {}).items():
            if code in bindings and isinstance(target, dict) and "double" in target:
                bindings[code]["double"] = token_of(target["double"])
    return bindings, device


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


def load_validator():
    for p in VALIDATOR_PATHS:
        if not os.path.isfile(p):
            continue
        ns = {"__file__": p}
        try:
            code = compile(open(p, encoding="utf-8").read(), p, "exec")
            exec(code, ns)
            return ns.get("validate"), ns.get("has_side_button_binding")
        except Exception as exc:
            print(f"[sidekeys] 校验器加载失败 {p}: {exc}", file=sys.stderr)
    return None, None


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
        self.set_default_size(580, 760)
        self.busy = False
        self.validate_fn, self.semantic_fn = load_validator()
        bindings, device = read_current()

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

        self.rows = {}
        for code, cn in BUTTONS:
            group = Adw.PreferencesGroup(title=f"{cn}（{code}）")
            for kind, gn in GESTURES:
                labels = ([NONE_LABEL] if kind in ("hold", "double") else []) + [lb for lb, _ in PRESETS]
                combo = Adw.ComboRow(title=gn)
                combo.set_model(Gtk.StringList.new(labels))
                combo.set_selected(self._preset_index(labels, bindings[code].get(kind), kind))
                combo.connect("notify::selected", self._on_choice, code, kind)
                group.add(combo)

                entry = Adw.EntryRow(title="xremap 写法，例如 C-c / A-Tab / Enter / []")
                entry.set_text(bindings[code].get(kind) or "")
                entry.connect("changed", lambda *_: self._update_preview())
                group.add(entry)

                self.rows[(code, kind)] = (combo, entry, labels)
            rows_box.append(group)

        dev_group = Adw.PreferencesGroup(title="作用范围")
        self.device_model = Gtk.StringList.new([])
        self.device_row = Adw.ComboRow(
            title="鼠标设备", subtitle="只接管选中的那一个，笔记本键盘和触控板不受影响"
        )
        self.device_row.set_model(self.device_model)
        self.device_row.connect("notify::selected", lambda *_: self._update_preview())
        dev_group.add(self.device_row)
        hint = Adw.PreferencesGroup()
        hint_row = Adw.ActionRow(title="双击走 experimental_map", subtitle="上游标注为实验特性，版本更新可能改格式；xremap 不支持在 experimental_map 里做设备匹配，范围由服务的 --device 保证")
        hint.add(hint_row)
        warn = Adw.PreferencesGroup()
        warn.add(Adw.ActionRow(title="回车类动作会发给当前焦点应用", subtitle="终端里是执行；在聊天框/表单里可能直接发送或提交"))
        rows_box.append(dev_group)
        rows_box.append(hint)
        rows_box.append(warn)

        adv_group = Adw.PreferencesGroup(title="将写入的 YAML", description="只读预览；点“应用”才会写入并生效")
        preview_scroll = Gtk.ScrolledWindow()
        preview_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # 外层本身就是可滚动容器，propagate_natural_height 会让这里按最小值算而被压扁；
        # 所以显式给 min/max 高度，并关掉 propagate。
        preview_scroll.set_propagate_natural_height(False)
        preview_scroll.set_min_content_height(200)
        preview_scroll.set_max_content_height(280)
        preview_scroll.set_size_request(-1, 200)
        preview_scroll.set_vexpand(False)
        self.preview_scroll = preview_scroll
        self.preview_text = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
        self.preview_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        preview_scroll.set_child(self.preview_text)
        adv_group.add(preview_scroll)
        rows_box.append(adv_group)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=580)
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
        for code, kind in [(c, k) for c, _ in BUTTONS for k, _ in GESTURES]:
            self._sync_custom_visibility(code, kind)
        self._update_preview()
        self._refresh_status()
        GLib.timeout_add_seconds(5, self._refresh_status)

    def _preset_index(self, labels, token, kind):
        want = token if token else (NONE_LABEL if kind in ("hold", "double") else None)
        if want is None:
            return labels.index(CUSTOM_LABEL)
        if want in labels:
            return labels.index(want)
        return labels.index(CUSTOM_LABEL) if CUSTOM_LABEL in labels else 0

    def _populate_devices(self, current):
        names = list_mice()
        if current and current not in names:
            names.insert(0, current)
        strings = names or [NO_DEVICE]
        for name in strings:
            self.device_model.append(name)
        target = current if current in strings else strings[0]
        self.device_row.set_selected(strings.index(target))

    def _selected_device(self):
        idx = self.device_row.get_selected()
        if idx < 0 or idx >= self.device_model.get_n_items():
            return None
        name = self.device_model.get_string(idx)
        return None if name == NO_DEVICE else name

    def _sync_custom_visibility(self, code, kind):
        combo, entry, labels = self.rows[(code, kind)]
        is_custom = combo.get_selected_item().get_string() == CUSTOM_LABEL
        entry.set_visible(is_custom)
        if not is_custom:
            entry.set_text("" if combo.get_selected_item().get_string() == NONE_LABEL else TOKEN_BY_LABEL[combo.get_selected_item().get_string()])

    def _on_choice(self, combo, _param, code, kind):
        self._sync_custom_visibility(code, kind)
        self._update_preview()

    def current_bindings(self):
        out = {}
        for code, _ in BUTTONS:
            out[code] = {}
            for kind, _gn in GESTURES:
                combo, entry, _labels = self.rows[(code, kind)]
                label = combo.get_selected_item().get_string()
                if label == CUSTOM_LABEL:
                    token = entry.get_text().strip()
                elif label == NONE_LABEL:
                    token = None
                else:
                    token = TOKEN_BY_LABEL[label]
                out[code][kind] = token
        return out

    def _describe(self, bindings):
        bits = []
        for code, cn in BUTTONS:
            parts = []
            for kind, gn in GESTURES:
                tok = bindings[code].get(kind)
                if tok:
                    parts.append(f"{gn}={LABEL_BY_TOKEN.get(tok, tok)}")
            if parts:
                bits.append(f"{cn} " + "/".join(parts))
        return " ／ ".join(bits)

    def _update_preview(self):
        bindings = self.current_bindings()
        self.preview_text.get_buffer().set_text(dump_yaml(bindings, self._selected_device()))
        self.title_widget.set_subtitle(self._describe(bindings))

    def _refresh_status(self):
        poll_service(self._on_status)
        return True

    def _on_status(self, props):
        state = props.get("ActiveState", "unknown")
        if state == "active":
            self.banner.set_revealed(False)
        else:
            self.banner.set_title(f"{UNIT} 未在运行（{state}，历史重启 {props.get('NRestarts', '?')} 次）")
            self.banner.set_button_label("启动服务")
            self.banner.set_revealed(True)

    def _on_apply(self, _btn):
        bindings = self.current_bindings()
        device = self._selected_device()
        bad = [
            f"{code}.{kind}"
            for code in bindings
            for kind, tok in bindings[code].items()
            if tok and not VALID_TOKEN.match(tok)
        ]
        if bad:
            self._toast(f"这几处写法有不被接受的字符：{', '.join(bad)}", error=True)
            return
        if not device:
            self._toast("没有可选的鼠标设备。本工具刻意只接管单个设备，不去抓全部键盘", error=True)
            return
        payload = dump_yaml(bindings, device)
        if "BTN_SIDE" not in payload and "BTN_EXTRA" not in payload:
            self._toast("生成的配置里没有任何侧键绑定，已中止", error=True)
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
        if self.validate_fn:
            errs = self.validate_fn(STAGE_PATH)
            if errs:
                self._toast("校验未通过：" + errs[0], error=True)
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
        toast.set_timeout(8 if error else 4)
        toast.set_priority(Adw.ToastPriority.HIGH if error else Adw.ToastPriority.NORMAL)
        self.toast.add_toast(toast)


def cli_mode(argv):
    bindings, device = read_current()
    if argv[0] == "--print":
        sys.stdout.write(dump_yaml(bindings, device))
        return 0
    if argv[0] == "--mice":
        print("\n".join(list_mice()))
        return 0
    print("用法: sidekeys [--print | --mice]", file=sys.stderr)
    return 2


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--print", "--mice"):
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
