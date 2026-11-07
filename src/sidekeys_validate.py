#!/usr/bin/env python3
"""sidekeys 的配置文件校验器。

为什么需要它：`xremap --validate-config` 会校验 keymap 的键名，但**不校验 modmap 的键名**——
实测把 `held: [TotallyBogus]` 写进去照样 “Config is valid”，于是错键名只会在运行时静默失效，
而界面上的 toast 还写着“已生效”。这个模块把键名检查补回来，并给出近似建议（Return → Enter）。

用法：
    python3 sidekeys_validate.py <config.yml>      # 通过返回 0，失败返回 1 并打印原因
也可以被 GUI import：validate(path) -> list[str]
"""

import difflib
import re
import sys

try:
    import evdev.ecodes as e
except ImportError:  # 没有 python-evdev 时退化为只做结构检查
    e = None
import yaml

TRIGGER_PREFIX = ("BTN_",)
MODIFIERS = {
    "c", "ctrl", "control", "c_l", "c_r", "ctrl_l", "ctrl_r", "control_l", "control_r",
    "s", "shift", "s_l", "s_r", "shift_l", "shift_r",
    "a", "alt", "m", "a_l", "a_r", "alt_l", "alt_r", "m_l", "m_r", "leftalt", "rightalt",
    "w", "super", "win", "windows", "super_l", "super_r", "win_l", "win_r",
    "windows_l", "windows_r", "leftmeta", "rightmeta",
    "altgr", "iso_level3_shift",
}
# 常见误写 → 正确写法（都是我自己踩过或文档里容易看错的）
CORRECTIONS = {
    "return": "Enter",
    "escape": "Esc",
    "backspace": "BackSpace",
    "del": "Delete",
    "spacebar": "Space",
    "ctrl": "Ctrl_L 或 Ctrl_R",
    "control": "Ctrl_L 或 Ctrl_R",
    "alt": "Alt_L 或 Alt_R",
    "shift": "Shift_L 或 Shift_R",
    "cmd": "Super_L",
    "option": "Alt_L",
    "pageup": "Page_Up",
    "pagedown": "Page_Down",
    "insert": "Insert",
    "side": "BTN_SIDE（触发键必须带 BTN_ 前缀）",
    "extra": "BTN_EXTRA（触发键必须带 BTN_ 前缀）",
}
BTN_CODES = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}
# xremap 自己的别名表（src/config/key.rs）把这些映射到 evdev 名，所以它们也是合法“键名”。
# 漏了这些就会误杀合法配置——我第一次就漏了 Ctrl_L。
XREMAP_KEY_ALIASES = {
    "shift_l", "shift_r", "s_l", "s_r",
    "control_l", "control_r", "ctrl_l", "ctrl_r", "c_l", "c_r",
    "alt_l", "alt_r", "a_l", "a_r", "m_l", "m_r",
    "super_l", "super_r", "windows_l", "windows_r", "win_l", "win_r", "w_l", "w_r",
    "any",
}


def _is_code_form(token):
    return bool(re.fullmatch(r"code_\d+|0x[0-9a-f]{1,4}", token.lower()))


def _known_names():
    if e is None:
        return set()
    names = set()
    for table in (getattr(e, "KEY", None), getattr(e, "BTN", None)):
        if not table:
            continue
        # evdev 的 ecodes.KEY / BTN 是「码 -> 名字」的映射，迭代它只会拿到整数，必须取 values()
        values = table.values() if hasattr(table, "values") else table
        for v in values:
            vs = v if isinstance(v, tuple) else (v,)
            for item in vs:
                if isinstance(item, str) and item:
                    names.add(item.lower())
    return names


def _name_ok(token, known):
    """token 是一个键名（不含修饰键前缀）。"""
    if not known:
        return True  # 无法核对时不误伤
    t = token.lower()
    if t in known or t in XREMAP_KEY_ALIASES or _is_code_form(t):
        return True
    if t.startswith(("key_", "btn_", "kcode_")):
        return t in known
    # 允许省略 KEY_ 前缀，但 BTN_ 前缀是必需的（xremap 文档明确写了）
    if ("key_" + t) in known:
        return True
    if t in BTN_CODES or ("btn_" + t) in known:
        return False  # 鼠标键必须写全 BTN_ 前缀
    return False


def _suggest(token, known):
    if token.lower() in CORRECTIONS:
        return CORRECTIONS[token.lower()]
    if not known:
        return None
    cand = difflib.get_close_matches(token.lower(), sorted(known), n=1, cutoff=0.72)
    return cand[0].upper() if cand else None


def check_token(token, where, errors, known):
    if not isinstance(token, str):
        errors.append(f"{where}: 期望字符串或字符串列表，实际是 {type(token).__name__}")
        return
    t = token.strip()
    if not t:
        errors.append(f"{where}: 空的映射目标")
        return
    if t == "[]":
        return  # 屏蔽该键
    parts = t.split("-")
    if len(parts) > 1:
        for mod in parts[:-1]:
            if mod.lower() not in MODIFIERS:
                s = _suggest(mod, known)
                errors.append(f"{where}: “{t}” 里的 “{mod}” 不是修饰键（可用 {', '.join(sorted(m for m in MODIFIERS if len(m) <= 4))}）"
                              + (f"，你想写的可能是 {s}" if s else ""))
                return
    key = parts[-1]
    if not _name_ok(key, known):
        s = _suggest(key, known)
        errors.append(f"{where}: “{key}” 不是可识别的键名" + (f"，你想写的可能是 “{s}”" if s else ""))


def _walk_action(value, where, errors, known):
    if value is None:
        return
    if isinstance(value, str):
        check_token(value, where, errors, known)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _walk_action(item, f"{where}[{i}]", errors, known)
    elif isinstance(value, dict):
        for k, v in value.items():
            if k in ("double", "timeout", "held", "alone", "tap", "sleep", "delay_millis",
                     "throttle_ms", "bypass", "escape_next_key", "repress", "remap", "keypress",
                     "set_mode", "launch", "close_apps", "cancel_on", "repeat", "press", "release"):
                if k in ("held", "alone", "double", "keypress", "repeat", "press", "release",
                         "remap", "launch", "close_apps"):
                    _walk_action(v, f"{where}.{k}", errors, known)
                elif k == "remap" and isinstance(v, dict):
                    for kk, vv in v.items():
                        check_token(kk, f"{where}.remap 触发键 {kk}", errors, known)
                        _walk_action(vv, f"{where}.remap[{kk}]", errors, known)
                continue
            errors.append(f"{where}: 不认识的键 “{k}”（本工具不生成它，请确认是否手改过配置）")
    else:
        errors.append(f"{where}: 不支持的值类型 {type(value).__name__}")


def _walk_block(block, section, errors, known):
    if not isinstance(block, dict):
        errors.append(f"{section}: 列表项必须是映射")
        return
    remap = block.get("remap")
    if remap is None:
        errors.append(f"{section}: 缺少 remap 字段")
        return
    if not isinstance(remap, dict):
        errors.append(f"{section}.remap: 必须是“键: 目标”的映射")
        return
    for trigger, target in remap.items():
        check_token(str(trigger), f"{section}.remap 触发键 {trigger}", errors, known)
        _walk_action(target, f"{section}.remap[{trigger}]", errors, known)


def validate(path):
    """返回错误列表；空列表表示可以安全写入。"""
    errors = []
    known = _known_names()
    try:
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except FileNotFoundError:
        return [f"文件不存在：{path}"]
    except yaml.YAMLError as exc:
        return [f"YAML 解析失败：{exc}"]
    if not isinstance(doc, dict):
        return ["配置顶层必须是映射（keymap/modmap/experimental_map）"]
    seen_any = False
    for section in ("keymap", "modmap", "experimental_map"):
        blocks = doc.get(section)
        if blocks is None:
            continue
        if not isinstance(blocks, list):
            errors.append(f"{section}: 必须是列表")
            continue
        for i, block in enumerate(blocks):
            _walk_block(block, f"{section}[{i}]", errors, known)
        seen_any = True
    if not seen_any:
        errors.append("配置里没有任何 keymap/modmap/experimental_map 区块")
    for section in set(doc) - {"keymap", "modmap", "experimental_map"}:
        errors.append(f"本工具不写未知区块：{section}")
    # 语义门：至少有一个侧键被绑定。xremap 认为“空配置”也合法，会静默取消所有映射。
    if not errors and not has_side_button_binding(doc):
        errors.append("两个侧键都没有任何绑定，拒绝写入（防止误清空现有映射）")
    return errors


def has_side_button_binding(doc):
    """语义门：至少有一个侧键真的被绑定了（keymap/modmap/experimental_map 任一处）。"""
    for section in ("keymap", "modmap", "experimental_map"):
        for block in doc.get(section) or []:
            if isinstance(block, dict) and any(
                str(k).upper() in ("BTN_SIDE", "BTN_EXTRA") for k in (block.get("remap") or {})
            ):
                return True
    return False


def main(argv):
    if len(argv) < 2:
        print("用法: sidekeys_validate.py <config.yml>", file=sys.stderr)
        return 2
    errs = validate(argv[1])
    for line in errs:
        print(line, file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
