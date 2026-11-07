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
import subprocess
import sys

try:
    import evdev.ecodes as e
except ImportError:  # 没有 python-evdev 时退化为只做结构检查
    e = None
import yaml

TRIGGER_PREFIX = ("BTN_",)
# 与 GUI 侧保持一致的长按阈值可用范围
HOLD_TIMEOUT_MIN = 100
HOLD_TIMEOUT_MAX = 3000
HOLD_TIMEOUT_SLOTS = (
    "tap_timeout_millis", "tap_timeout", "alone_timeout_millis",
    "hold_threshold_millis", "held_threshold_millis",
)
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
MODIFIER_KEY_NAMES = {
    "CTRL_L", "CTRL_R", "CONTROL_L", "CONTROL_R", "C_L", "C_R",
    "SHIFT_L", "SHIFT_R", "S_L", "S_R",
    "ALT_L", "ALT_R", "A_L", "A_R", "M_L", "M_R",
    "SUPER_L", "SUPER_R", "WIN_L", "WIN_R", "W_L", "W_R",
    "WINDOWS_L", "WINDOWS_R", "LEFTCTRL", "RIGHTCTRL", "LEFTSHIFT", "RIGHTSHIFT",
    "LEFTALT", "RIGHTALT", "LEFTMETA", "RIGHTMETA",
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
                     "set_mode", "launch", "close_apps", "cancel_on", "repeat", "press", "release",
                     "tap_timeout_millis", "tap_timeout", "alone_timeout_millis",
                     "hold_threshold_millis", "held_threshold_millis",
                     "free_hold", "interruptable"):
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


def _check_modmap_shape(code, target, errors):
    """modmap 的 alone/held 有自己的坑，单独查。

    实测：`alone: S-Insert` 会被 xremap 静默忽略（modmap 不校验键名，--validate-config
    也照样通过），必须写成 [SHIFT_L, Insert]；而数组是“同时按下”，不是宏序列。
    """
    if not isinstance(target, dict):
        return
    for slot in ("alone", "held"):
        if slot not in target:
            continue
        value = target[slot]
        where = f"{code}.{slot}"
        if isinstance(value, str):
            if value == "[]":
                continue
            if "-" in value:
                errors.append(f"modmap {where}: “{value}” 这种组合字符串不会被 modmap 接受（实测被静默忽略），"
                              f"请写成数组，例如 [SHIFT_L, Insert]")
            check_token(value, f"modmap {where}", errors, _known_names())
        elif isinstance(value, list):
            mods = [v for v in value if str(v).upper() in MODIFIER_KEY_NAMES]
            keys = [v for v in value if str(v).upper() not in MODIFIER_KEY_NAMES]
            if len(keys) > 1:
                errors.append(f"modmap {where}: 数组里的多个普通键是“同时按下”，不是宏序列；"
                              f"要序列请放进点按（keymap）")
            for item in value:
                check_token(str(item), f"modmap {where} 数组项", errors, _known_names())
    # 长按阈值：xremap 只在松手时判定长按（鼠标键不发 repeat 事件，Tick 对 modmap 无效），
    # 所以“按住多久算长按”是这里唯一有意义的旋钮。
    for slot in HOLD_TIMEOUT_SLOTS:
        if slot not in target:
            continue
        value = target[slot]
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"modmap {code}.{slot}: 必须是整数毫秒，收到 {value!r}")
        elif not HOLD_TIMEOUT_MIN <= value <= HOLD_TIMEOUT_MAX:
            errors.append(f"modmap {code}.{slot}: {value}ms 超出可用范围 "
                          f"{HOLD_TIMEOUT_MIN}~{HOLD_TIMEOUT_MAX}ms")
    if target.get("free_hold") is True:
        errors.append(f"modmap {code}.free_hold: true 会让这个鼠标侧键的长按永远不触发——"
                      f"它靠 repeat 事件提前生效，而鼠标键不发 repeat 事件（上游注释确认）")


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
        if section.startswith("modmap"):
            _check_modmap_shape(str(trigger), target, errors)
        _walk_action(target, f"{section}.remap[{trigger}]", errors, known)


# ------------------------------------------------------------------ keyd 路线
#
# keyd 的配置是 INI，不是 YAML，所以整个校验逻辑另起一套。它对坏配置的态度是
# “打印 ERROR: line n: ... 然后继续跑”，服务 active 完全不代表配置生效，
# 因此这一层的拒绝必须比它更早、更严。

KEYD_ID_FORM = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$")
KEYD_SIDE_BUTTONS = ("mouse1", "mouse2")
KEYD_KNOWN_KEYS = {"mouse1", "mouse2", "mouse3", "mouse4", "mouse5"}
# man「LAYERS」原文：C=Control, M=Meta/Super, A=Alt, S=Shift, G=AltGr
KEYD_MOD_PREFIXES = {"C", "S", "A", "M", "G"}
KEYD_OPERATORS = {"timeout", "macro", "macro2"}
KEYD_OTHER_ACTIONS = {"layer", "layerm", "oneshot", "oneshotm", "swap", "swapm", "toggle",
                      "togglem", "clear", "clearm", "overload", "overloadt", "overloadt2",
                      "overloadi", "lettermod", "setlayout", "command", "noop"}
KEYD_NOOP = "noop"
_keyd_names_cache = {}


def _keyd_names():
    """keyd 自己报告的合法键名；拿不到就返回 None，那时跳过键名检查而不是误杀。"""
    if "names" in _keyd_names_cache:
        return _keyd_names_cache["names"]
    names = None
    for binary in ("/usr/bin/keyd.rvaiya", "/usr/bin/keyd"):
        try:
            proc = subprocess.run([binary, "list-keys"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            names = set(proc.stdout.split())
            break
    _keyd_names_cache["names"] = names
    return names


def _split_top_commas(inner):
    out, depth, cur = [], 0, ""
    for ch in inner:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def _check_keyd_expr(expr, where, errors, names):
    expr = expr.strip()
    if not expr:
        errors.append(f"{where}: 表达式为空")
        return
    m = re.match(r"^(\w+)\((.*)\)$", expr, re.S)
    if m:
        op, inner = m.group(1), m.group(2)
        if op in KEYD_OTHER_ACTIONS and op not in KEYD_OPERATORS:
            errors.append(f"{where}: 本工具不生成 {op}(...)，请确认是否手改过配置")
            return
        if op == "timeout":
            parts = _split_top_commas(inner)
            if len(parts) != 3:
                errors.append(f"{where}: timeout(点按, 毫秒, 长按) 需要 3 个参数，收到 {len(parts)} 个")
                return
            _check_keyd_expr(parts[0], f"{where}.点按", errors, names)
            ms = parts[1].strip()
            if not ms.isdigit():
                errors.append(f"{where}: timeout 的第二个参数必须是整数毫秒，收到 {ms!r}")
            elif not HOLD_TIMEOUT_MIN <= int(ms) <= HOLD_TIMEOUT_MAX:
                errors.append(f"{where}: 长按阈值 {ms}ms 超出可用范围 {HOLD_TIMEOUT_MIN}~{HOLD_TIMEOUT_MAX}ms")
            _check_keyd_expr(parts[2], f"{where}.长按", errors, names)
            return
        if op == "macro2":
            parts = _split_top_commas(inner)
            if len(parts) != 3:
                errors.append(f"{where}: macro2(超时, 重复超时, 内容) 需要 3 个参数，收到 {len(parts)} 个")
                return
            for i, val in enumerate(parts[:2]):
                if not val.strip().isdigit():
                    errors.append(f"{where}: macro2 的第 {i + 1} 个参数必须是整数毫秒，收到 {val.strip()!r}")
            _check_keyd_expr(parts[2], f"{where}.内容", errors, names)
            return
        # macro( … ) 内部是空格分隔的 token
        for i, tok in enumerate(inner.split()):
            _check_keyd_expr(tok, f"{where}.序列[{i}]", errors, names)
        return

    if expr == KEYD_NOOP:
        return
    parts = [x for x in expr.split("-") if x]
    if not parts:
        errors.append(f"{where}: 无法解析的写法 {expr!r}")
        return
    for prefix in parts[:-1]:
        if prefix.upper() not in KEYD_MOD_PREFIXES:
            errors.append(f"{where}: “{expr}” 里的 “{prefix}” 不是 keyd 的修饰前缀（只有 C/S/A/M/G）")
    base = parts[-1].lower()
    if names is not None and base not in names:
        cand = difflib.get_close_matches(base, sorted(names), n=1, cutoff=0.72)
        hint = f"，是否想写 {cand[0]}？" if cand else ""
        errors.append(f"{where}: keyd 不认得键名 “{base}”{hint}")


def _validate_keyd(text):
    errors = []
    names = _keyd_names()
    ids, bindings, section = [], {}, None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section is None:
            errors.append("keyd 配置必须以 [ids] 开头")
            continue
        if section == "ids":
            ids.append(line)
            continue
        if section != "main":
            errors.append(f"本工具不写区块 [{section}]，请确认是否手改过配置")
            continue
        if "=" not in line:
            errors.append(f"[main]: 无法解析的行 {line!r}")
            continue
        lhs, rhs = [x.strip() for x in line.split("=", 1)]
        bindings[lhs] = rhs

    if not ids:
        errors.append("[ids] 为空：keyd 会接管所有输入设备，本工具刻意只接管单个设备")
    for ident in ids:
        if ident == "*":
            errors.append("[ids]: 不允许 * —— 接管范围必须显式")
        elif not KEYD_ID_FORM.match(ident):
            errors.append(f"[ids]: “{ident}” 不是 vid:pid 形式（keyd 实测按 vid:pid 前缀匹配）")
    if not any(k in bindings for k in KEYD_SIDE_BUTTONS):
        errors.append("[main]: 两个侧键（mouse1/mouse2）都没有绑定，拒绝写入")
    for lhs, rhs in bindings.items():
        if lhs not in KEYD_KNOWN_KEYS:
            errors.append(f"[main]: 本工具不生成 {lhs} 的绑定（只写 mouse1/mouse2）")
            continue
        _check_keyd_expr(rhs, f"[main] {lhs}", errors, names)
    return errors


def looks_like_keyd(text):
    """有 INI 区块头，或者出现了 mouseN = 这种 keyd 绑定行，就按 keyd 规则校验。

    光看区块头不够：文件被截断或手改坏了时会只剩裸的绑定行，
    那种情况必须给出 keyd 的报错，而不是 “顶层必须是映射” 这种 YAML 味道的误导。
    """
    for line in text.splitlines():
        if re.match(r"^\s*\[\s*(ids|main|global)\s*\]", line):
            return True
        if re.match(r"^\s*mouse[0-9]+\s*=", line):
            return True
    return False


def validate(path):
    """返回错误列表；空列表表示可以安全写入。"""
    errors = []
    known = _known_names()
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return [f"文件不存在：{path}"]
    # keyd 的配置是 INI，走完全另一套规则
    if looks_like_keyd(text):
        return _validate_keyd(text)
    try:
        doc = yaml.safe_load(text)
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
