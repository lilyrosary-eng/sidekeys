#!/usr/bin/env python3
"""侧键映射 —— xremap 两个鼠标侧键的图形配置器。

每个侧键可以绑三个手势：点按 / 长按 / 双击。
不做实时按键捕获：改动只在按“应用”时一次性写入配置并重启服务。
所有跨进程操作都在后台线程里跑，结果用 idle_add 送回主循环，界面不会冻结。
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import yaml
from gi.repository import Adw, Gio, GLib, Gtk

CONFIG_PATH = "/etc/xremap/config.yml"
STAGE_PATH = f"/tmp/sidekeys-stage-{os.getuid()}.yml"
# 规范路径按 Debian 约定放在 /usr/lib/sidekeys（包不能往 /usr/local 写东西）；
# /usr/local 那一组是旧版 install.sh 留下的位置，只作兼容回退：
# 老机器上的 polkit 策略里 exec.path 指的正是旧路径，回退能让“GUI 已升级、root 侧还没跑”
# 这段窗口期仍然可用，而不是莫名被授权拒绝。
APPLY_HELPER_CANDIDATES = ("/usr/lib/sidekeys/sidekeys-apply", "/usr/local/bin/sidekeys-apply")
# 先找与 GUI 同目录的那一份：规则必须和生成器同版本，
# 否则旧的系统校验器会把新版合法写法（例如 tap_timeout_millis）判成未知键。
VALIDATOR_PATHS = (os.path.join(os.path.dirname(os.path.abspath(__file__)), "sidekeys_validate.py"),
                   "/usr/lib/sidekeys/sidekeys_validate.py",
                   "/usr/local/lib/sidekeys/sidekeys_validate.py")
UNIT = "sidekeys"
XREMAP_UNIT = "sidekeys"       # 我们自己的 xremap 服务单元
KEYD_UNIT = "keyd"             # keyd 的上游单元；切后端时二选一运行
KEYD_CONFIG_PATH = "/etc/keyd/sidekeys.conf"
NO_DEVICE = "（没检测到鼠标设备）"
NONE_LABEL = "（不启用）"
DOUBLE_TIMEOUT = 200
# 长按阈值（tap_timeout_millis）：按住超过这个时长再松手才判定为“长按”。
# 上游默认 1000ms，对手势来说太长；下限 100ms 是因为比这更短的按住
# 和普通点击已经分不开了，上限 3000ms 是因为再长就没人会这么用。
HOLD_TIMEOUT_DEFAULT = 400
HOLD_TIMEOUT_MIN = 100
HOLD_TIMEOUT_MAX = 3000

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

# 用户自定义预设：与 Windows 版同一套 schema（presets.json 里是 [{"name","token"}]），
# 落地位置按 XDG 习惯放在 ~/.config/sidekeys/。
USER_PRESETS = []          # [(名称, 写法)]，插在“自定义…”之前
TOKEN_BY_LABEL = dict(PRESETS)
LABEL_BY_TOKEN = {v: k for k, v in PRESETS if v}


def presets_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "sidekeys", "presets.json")


def preset_items():
    return PRESETS[:-1] + list(USER_PRESETS) + [PRESETS[-1]]


def load_user_presets():
    """读 presets.json。坏文件、坏条目一律跳过：它不该有能力挡住界面启动。"""
    USER_PRESETS.clear()
    try:
        with open(presets_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    if not isinstance(data, list):
        return
    builtin = {lb for lb, _tok in PRESETS}
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        token = item.get("token")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(token, str) or not token.strip():
            continue
        clean_name, clean_token = name.strip(), token.strip()
        if clean_name in builtin or any(clean_name == n for n, _t in USER_PRESETS):
            continue
        USER_PRESETS.append((clean_name, clean_token))


def save_user_presets():
    path = presets_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump([{"name": n, "token": t} for n, t in USER_PRESETS], fh,
                  ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def rebuild_preset_tables():
    """把用户预设并进查找表；keyd 的反向表也要重建，否则读回时用户预设会退化成“自定义”。"""
    global TOKEN_BY_LABEL, LABEL_BY_TOKEN, _KEYD_TO_TOKEN
    TOKEN_BY_LABEL = dict(preset_items())
    LABEL_BY_TOKEN = {v: k for k, v in preset_items() if v}
    if "_KEYD_TO_TOKEN" in globals():
        _KEYD_TO_TOKEN = {to_keyd_expr(tok): tok for _lb, tok in preset_items() if tok and tok != "[]"}
        _KEYD_TO_TOKEN[KEYD_NOOP] = "[]"

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


MOD_ALIASES = {
    "c": "CTRL_L", "ctrl": "CTRL_L", "control": "CTRL_L",
    "s": "SHIFT_L", "shift": "SHIFT_L",
    "a": "ALT_L", "alt": "ALT_L", "m": "ALT_L",
    "w": "SUPER_L", "super": "SUPER_L", "win": "SUPER_L", "windows": "SUPER_L",
}
MOD_NAMES = {"CTRL_L", "CTRL_R", "SHIFT_L", "SHIFT_R", "ALT_L", "ALT_R",
             "SUPER_L", "SUPER_R", "M_L", "M_R", "W_L", "W_R", "A_L", "A_R",
             "C_L", "C_R", "S_L", "S_R"}
SEQUENCE_IN_MODMAP = "modmap 的 alone/held 只能表达“同时按下”，不能写逗号分隔的宏序列；宏请放在点按里"
MOD_SPAN_WARN = "数组里同时有修饰键和多个普通键时，修饰键会一直按住到最后那个键（例如 [SHIFT_L, Insert, Enter] 会变成 Shift+Enter）"


def split_combo(token):
    """把 keymap 风格的组合（C-c / Shift-A-Tab）拆成 modmap 需要的数组形式。"""
    parts = [p.strip() for p in token.split("-")]
    base = parts[-1]
    mods = []
    for p in parts[:-1]:
        alias = MOD_ALIASES.get(p.lower())
        if not alias:
            raise ValueError(f"“{token}”里的“{p}”不是可识别的修饰键")
        mods.append(alias)
    return mods, base


def _chord(token):
    """modmap 的 alone/held 值：组合必须写成 [CTRL_L, c] 这种数组。

    实测 `alone: S-Insert` 会被 xremap 静默忽略（modmap 不校验键名，--validate-config
    也照样说 valid），只有 `alone: [SHIFT_L, Insert]` 才真的发事件。
    """
    if token is None or token == "" or token == "[]":
        return []
    if "," in token:
        raise ValueError(SEQUENCE_IN_MODMAP)
    mods, base = split_combo(token)
    if not mods:
        return base
    return mods + [base]


def _unchord(value):
    """把 modmap 数组形式还原成界面上的组合写法，保证读回一致。"""
    if value is None:
        return None
    if value == []:
        return "[]"
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if len(value) == 1:
            return str(value[0])
        parts = [str(v) for v in value]
        base = parts[-1]
        mods = []
        for m in parts[:-1]:
            key = m.upper()
            if key not in MOD_NAMES:
                return ", ".join(parts)  # 不是纯修饰键前缀，原样显示
            mods.append({"CTRL": "C", "SHIFT": "S", "ALT": "A", "SUPER": "Super"}[key.split("_")[0]])
        return "-".join(mods + [base])
    return str(value)


def _hold_timeout_ms(value):
    if value is None:
        # 从线上配置读回来的“没写过阈值”是 None。这条路径不经过界面的 SpinRow，
        # 不补齐的话 `sidekeys --print` 会直接抛 ValueError。
        return HOLD_TIMEOUT_DEFAULT
    try:
        ms = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"长按阈值必须是整数毫秒，收到 {value!r}") from None
    if not HOLD_TIMEOUT_MIN <= ms <= HOLD_TIMEOUT_MAX:
        raise ValueError(f"长按阈值只能在 {HOLD_TIMEOUT_MIN}~{HOLD_TIMEOUT_MAX} 毫秒之间")
    return ms


# ---------------------------------------------------------------- keyd 后端
#
# 为什么要有第二个后端：xremap 的 modmap 对鼠标键只能在松手时发出长按动作
# （进入 HoldDown 只靠 repeat 事件，而鼠标键不发 repeat）。keyd 的定时器在
# 自己的事件循环里（evloop.c: poll 带 timeout → EV_TIMEOUT；keyboard.c:1064
# `if (time >= pending_key.expire) action = action2` 后同步 process_descriptor），
# 实测长按比物理松手早 956ms 发出。代价：keyd 没有双击算子（man 里只有 layer
# toggle 的“按两次”），所以切到 keyd 就必须放弃双击手势。
XREMAP_BACKEND = "xremap"
KEYD_BACKEND = "keyd"
BACKENDS = [
    (XREMAP_BACKEND, "xremap（支持双击；长按要等松手才发）"),
    (KEYD_BACKEND, "keyd（长按到点即发；不支持双击）"),
]
BACKEND_BY_LABEL = {label: name for name, label in BACKENDS}
LABEL_BY_BACKEND = dict(BACKENDS)
XREMAP_LABEL = LABEL_BY_BACKEND[XREMAP_BACKEND]   # 界面下拉上的字面值，测试也用得上
KEYD_LABEL = LABEL_BY_BACKEND[KEYD_BACKEND]

# 实测映射：device.c 把 BTN_SIDE→KEYD_MOUSE_1、BTN_EXTRA→KEYD_MOUSE_2，
# 且 keyd monitor 确实按 mouse1 上报这只鼠标的侧键。
KEYD_BUTTON = {"BTN_SIDE": "mouse1", "BTN_EXTRA": "mouse2"}
# man「LAYERS」原文的修饰前缀：C=Control, M=Meta/Super, A=Alt, S=Shift, G=AltGr
KEYD_MOD_PREFIXES = {
    "C": "C", "CTRL": "C", "CONTROL": "C",
    "S": "S", "SHIFT": "S",
    "A": "A", "ALT": "A",
    "M": "M", "W": "M", "WIN": "M", "WINDOWS": "M", "SUPER": "M",
    "G": "G", "ALTGR": "G",
}
KEYD_NOOP = "noop"          # keys.c: [KEYD_NOOP] = { "noop", ... }，用来表达“屏蔽”


def _keyd_chord(token):
    parts = [p.strip() for p in token.split("-")]
    base = parts[-1]
    if not base:
        raise ValueError(f"“{token}”缺少主键")
    letters = []
    for prefix in parts[:-1]:
        letter = KEYD_MOD_PREFIXES.get(prefix.upper())
        if not letter:
            raise ValueError(f"“{token}”里的“{prefix}”不是 keyd 认得的修饰键（keyd 只有 C/S/A/M/G）")
        letters.append(letter)
    return "-".join(letters + [base.lower()])


def to_keyd_expr(token, one_shot=False):
    """界面写法（xremap 风格）→ keyd 表达式。对已经是 keyd 风格的输入是幂等的。"""
    if token is None or token == "":
        return None
    if token == "[]":
        return KEYD_NOOP
    parts = [p.strip() for p in token.split(",") if p.strip()]
    if len(parts) > 1:
        expr = "macro(%s)" % " ".join(to_keyd_expr(part) for part in parts)
    else:
        expr = _keyd_chord(parts[0])
    if one_shot:
        # macro2 的 timeout 取 0 即关闭宏重复（man 原文）；实测关掉后 value=2 计数为 0
        expr = f"macro2(0, 0, {expr})"
    return expr


KEYD_ID_FORM = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$")


def dump_keyd(bindings, device, hold_timeout=HOLD_TIMEOUT_DEFAULT, auto_release=True):
    """device 可以是鼠标名（界面里的值）也可以是已经解析好的 vid:pid。"""
    # 阈值只在真的有长按时才求值：没绑长按的配置里凭空多一个数字没有意义
    ms = _hold_timeout_ms(hold_timeout) if any(b.get("hold") for b in bindings.values()) else None
    if not device:
        raise ValueError("keyd 后端必须指定设备，否则会改到所有输入设备")
    device_id = device if KEYD_ID_FORM.match(device) else device_id_for(device)
    lines = ["# 由侧键映射工具生成。手改前先用 sidekeys --print 看现状。", "", "[ids]", "", device_id, "", "[main]", ""]
    for code, cn in BUTTONS:
        key = KEYD_BUTTON[code]
        b = bindings[code]
        tap, hold, dbl = b.get("tap"), b.get("hold"), b.get("double")
        if dbl:
            raise ValueError("keyd 没有双击算子：请在界面里清空双击，或改用 xremap 后端")
        lines.append(f"# {cn}")
        if hold:
            tap_expr = to_keyd_expr(tap) if tap else KEYD_NOOP
            lines.append(f"{key} = timeout({tap_expr}, {ms}, {to_keyd_expr(hold, one_shot=auto_release)})")
        elif tap:
            lines.append(f"{key} = {to_keyd_expr(tap)}")
        else:
            lines.append(f"# {key} = 未绑定")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _split_top_level(text):
    """按逗号切，但忽略括号内的逗号（timeout(a, ms, b) 这种）。"""
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _strip_one_shot(expr):
    """macro2(0, 0, X) → (X, True)；其他原样返回。"""
    expr = expr.strip()
    m = re.match(r"^macro2\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(.+)\)$", expr, re.S)
    if m:
        return m.group(3).strip(), True
    return expr, False


_KEYD_TO_TOKEN = {to_keyd_expr(tok): tok for _lb, tok in preset_items() if tok and tok != "[]"}
_KEYD_TO_TOKEN[KEYD_NOOP] = "[]"


def _keyd_display(expr):
    """keyd 表达式 → 界面里显示的写法；不认识的自定义写法原样显示。"""
    expr, _one_shot = _strip_one_shot(expr)
    if expr in _KEYD_TO_TOKEN:
        return _KEYD_TO_TOKEN[expr]
    if expr.startswith("macro(") and expr.endswith(")"):
        # macro 内部是空格分隔的 token 序列，不是逗号
        inner = expr[len("macro("):-1].split()
        return ", ".join(_keyd_display(part) for part in inner)
    return expr


def parse_keyd(text):
    """解析 keyd 配置 → (设备 id, {BTN_*: {tap, hold, double}}, 阈值毫秒, 自动释放)"""
    device = None
    bindings = {k: dict(v) for k, v in DEFAULT_BINDINGS.items()}
    hold_timeout = None
    section = None
    hold_seen = one_shot_seen = False
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if "=" not in line:
            if section == "ids":
                device = device or line
            continue
        lhs, rhs = (p.strip() for p in line.split("=", 1))
        if section != "main":
            continue
        code = next((c for c, k in KEYD_BUTTON.items() if k == lhs), None)
        if code is None:
            continue
        if rhs.startswith("timeout(") and rhs.endswith(")"):
            args = _split_top_level(rhs[len("timeout("):-1])
            if len(args) == 3:
                tap_expr, ms_expr, hold_expr = args
                bindings[code]["tap"] = None if tap_expr == KEYD_NOOP else _keyd_display(tap_expr)
                bindings[code]["hold"] = _keyd_display(hold_expr)
                hold_seen = True
                one_shot_seen = one_shot_seen or _strip_one_shot(hold_expr)[1]
                try:
                    hold_timeout = int(ms_expr)
                except ValueError:
                    hold_timeout = None
        else:
            bindings[code]["tap"] = _keyd_display(rhs)
            bindings[code]["hold"] = None
    # 没绑过长按时无所谓；绑过的话，只要有一个是 macro2 包裹就认为开了自动释放
    return device, bindings, hold_timeout, (not hold_seen) or one_shot_seen


def dump_config(backend, bindings, device, hold_timeout=HOLD_TIMEOUT_DEFAULT, auto_release=True):
    if backend == KEYD_BACKEND:
        return dump_keyd(bindings, device, hold_timeout, auto_release)
    return dump_yaml(bindings, device, hold_timeout)


def dump_yaml(bindings, device, hold_timeout=HOLD_TIMEOUT_DEFAULT):
    keymap_remap = {}
    modmap_remap = {}
    expmap_remap = {}
    for code, _ in BUTTONS:
        b = bindings[code]
        tap, hold, dbl = b.get("tap"), b.get("hold"), b.get("double")
        if hold:
            # 有长按时，点按走 modmap 的 alone，避免同一键在两层各说一遍。
            # tap_timeout_millis 必写：默认的 1000ms 意味着“按住 1 秒以内松手都算点按”，
            # 想快速长按的人会被判成粘贴。
            modmap_remap[code] = {
                "alone": _chord(tap),
                "held": _chord(hold),
                "tap_timeout_millis": _hold_timeout_ms(hold_timeout),
            }
        elif tap is not None:
            keymap_remap[code] = _val(tap) if tap else []
        if dbl:
            if "," in dbl or "-" in dbl:
                raise ValueError("双击只支持单个键名（例如 Enter、F13），不支持组合或宏序列")
            expmap_remap[code] = {"double": dbl, "timeout": DOUBLE_TIMEOUT}

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


# 测试用注入点：这台机器可能正跑着 keyd，而用例要验证的是 xremap 分支，
# 只能显式指定后端，否则 read_current 会去读线上的 keyd 配置、用例集体跑偏。
BACKEND_OVERRIDE = None


def service_active(unit):
    try:
        proc = subprocess.run(["systemctl", "is-active", "--quiet", unit], timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def detect_backend():
    """当前生效的后端以“谁在跑”为准，而不是某个文件里写过什么。

    两个引擎都要 EVIOCGRAB 同一只鼠标，同时跑必然有一个抢不到；
    所以 GUI 必须按活跃服务来决定读哪份配置、写哪份配置。
    """
    if service_active(KEYD_UNIT):
        return KEYD_BACKEND
    return XREMAP_BACKEND


def read_current():
    """返回 (绑定, 设备名, 长按阈值, 后端, 长按自动释放)。"""
    backend = BACKEND_OVERRIDE or detect_backend()
    if backend == KEYD_BACKEND:
        try:
            with open(KEYD_CONFIG_PATH, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return {k: dict(v) for k, v in DEFAULT_BINDINGS.items()}, None, None, backend, True
        vidpid, bindings, hold_timeout, auto_release = parse_keyd(text)
        device = device_name_for_id(vidpid) if vidpid else None
        return bindings, device, hold_timeout, backend, auto_release

    bindings = {k: dict(v) for k, v in DEFAULT_BINDINGS.items()}
    device = None
    hold_timeout = None
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return bindings, device, hold_timeout, backend, True

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
                bindings[code]["tap"] = _unchord(target["alone"])
            if "held" in target:
                bindings[code]["hold"] = _unchord(target["held"])
            if hold_timeout is None and isinstance(target.get("tap_timeout_millis"), int):
                hold_timeout = target["tap_timeout_millis"]
        only = (block.get("device") or {}).get("only")
        if only and not device:
            device = str(only[0])
    for block in doc.get("experimental_map") or []:
        for code, target in (block.get("remap") or {}).items():
            if code in bindings and isinstance(target, dict) and "double" in target:
                bindings[code]["double"] = token_of(target["double"])
    return bindings, device, hold_timeout, backend, True


def _mouse_chunks():
    """逐个产出 (设备名, sysfs 路径)，只留真正带 mouse handler 的物理设备。"""
    try:
        with open("/proc/bus/input/devices", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return
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
            yield name.group(1), (sysfs.group(1) if sysfs else "")


def mouse_ids():
    """鼠标名 → vid:pid。keyd 的 [ids] 只认 id；实测 vid:pid 前缀就能命中该设备的接口。"""
    out = {}
    for name, sysfs in _mouse_chunks():
        m = re.search(r"([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\.[0-9a-fA-F]{4}", sysfs)
        if m:
            out[name] = f"{m.group(1)}:{m.group(2)}".lower()  # keyd 自己报的是小写十六进制
    return out


def device_id_for(name):
    vidpid = mouse_ids().get(name)
    if not vidpid:
        raise ValueError(f"没能从 /proc/bus/input/devices 解析出“{name}”的 vid:pid，keyd 后端不能盲写 ids")
    return vidpid


def device_name_for_id(vidpid):
    for name, found in mouse_ids().items():
        if found == vidpid:
            return name
    return vidpid


def list_mice():
    return [name for name, _sysfs in _mouse_chunks()]


def helper_supports_keyd():
    """线上那份 root 助手是不是已经会处理 keyd 分支。

    升级期一定会出现“GUI 新、助手旧”的组合：旧助手只吃 3 个参数，
    把 keyd 的 INI 当 xremap 的 YAML 写下去，后果是服务起不来。
    读不到文件时保守返回 True（让助手自己判），读到但没有分支就明确拒绝。
    """
    try:
        with open(apply_helper(), encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return True
    return "apply_keyd" in text


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


def apply_helper():
    """第一个真实存在的 root 助手路径。

    两条安装路线（deb 与 install.sh）的先后顺序不可预知，GUI 必须跟着文件走
    而不是跟着常量走：指向一个不存在的脚本时，polkit 只会给一句难懂的授权失败。
    """
    for path in APPLY_HELPER_CANDIDATES:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return APPLY_HELPER_CANDIDATES[0]


def self_fingerprint():
    """磁盘上这份代码的指纹，用来识别「窗口跑的是旧版」。

    为什么需要：安装是原地覆盖文件，旧进程内存里仍是旧代码，而重新执行
    sidekeys 只会把激活请求转交给那个旧进程（单实例 app id），于是新代码
    永远进不了场。实际踩到的症状：旧窗口生成旧格式 YAML（modmap 写组合键
    字符串），被磁盘上的新校验器拒掉，用户只看到「点应用没反应」。
    """
    try:
        path = getattr(sys.modules.get("__main__"), "__file__", None) or __file__
        with open(os.path.realpath(path), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


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


SERVICE_SNAPSHOT = """
for u in "$@"; do
  printf '%s\\t%s\\t%s\\n' "$u" \\
    "$(systemctl is-active "$u")" \\
    "$(systemctl show "$u" -p NRestarts --value 2>/dev/null)"
done
true
"""

# `systemctl show a b -p ActiveState` 在本机 systemd 上不给每个块标单元名
# （实测只打印两行 ActiveState 中间夹一个空行），所以不能靠位置归属：
# 少装一个、被 mask 一个，块数就错位，报错报到的单元等于骗用户。
# 这里改成每行自带单元名的快照，一次调用问两个单元。
def parse_service_lines(text):
    states = {}
    for line in (text or "").splitlines():
        cols = line.split("\t")
        if len(cols) != 3:
            continue
        unit, active, restarts = cols
        states[unit] = {"ActiveState": active, "SubState": "", "NRestarts": restarts}
    return states


def poll_service(callback):
    run_in_thread(
        ["sh", "-c", SERVICE_SNAPSHOT, "sidekeys-snapshot", XREMAP_UNIT, KEYD_UNIT],
        lambda rc, out, err: callback(parse_service_lines(out)),
        timeout=10,
    )

class SideKeysWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(580, 760)
        self.busy = False
        self.validate_fn, self.semantic_fn = load_validator()
        bindings, device, hold_timeout, backend, auto_release = read_current()
        self.hold_timeout = hold_timeout if isinstance(hold_timeout, int) else HOLD_TIMEOUT_DEFAULT
        self.backend = backend if backend in dict(BACKENDS) else XREMAP_BACKEND
        self.auto_release = bool(auto_release)

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
        # 每个分支会改写 action-name；start 必须带上当前选中的后端，
        # 否则助手会去启动“正在跑的那一个”，等于原地踏步（这就是用户看到的“点了没变化”）
        start_action = Gio.SimpleAction.new("start-service", None)
        start_action.connect("activate", lambda *_: self._dispatch(["start", self.backend], "服务已启动"))
        self.add_action(start_action)
        apply_action = Gio.SimpleAction.new("apply-current", None)
        apply_action.connect("activate", lambda *_: self._on_apply(None))
        self.add_action(apply_action)
        self.banner.set_property("action-name", "win.start-service")

        # 旧窗口横幅：宁可挡在应用前面，也不要让「点应用没反应」变成猜谜
        self.fingerprint = self_fingerprint()
        self.stale_banner = Adw.Banner()
        self.stale_banner.set_title("这个窗口跑的是旧版本代码，磁盘上的工具已经更新")
        self.stale_banner.set_button_label("重启工具")
        restart_action = Gio.SimpleAction.new("restart-tool", None)
        restart_action.connect("activate", self._on_restart)
        self.add_action(restart_action)
        self.stale_banner.set_property("action-name", "win.restart-tool")
        GLib.timeout_add_seconds(3, self._check_stale)

        rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        rows_box.set_margin_top(12)
        rows_box.set_margin_bottom(24)
        rows_box.set_margin_start(12)
        rows_box.set_margin_end(12)

        self.rows = {}
        self.double_rows = []
        for code, cn in BUTTONS:
            group = Adw.PreferencesGroup(title=f"{cn}（{code}）")
            for kind, gn in GESTURES:
                labels = ([NONE_LABEL] if kind in ("hold", "double") else []) + [lb for lb, _ in preset_items()]
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
                if kind == "double":
                    self.double_rows.append((code, combo, entry))
            rows_box.append(group)

        # 自定义预设：与 Windows 版同一套 presets.json 结构，保存即进所有下拉框
        preset_group = Adw.PreferencesGroup(
            title="预设",
            description="保存后会出现在所有手势的下拉框里，下次启动仍然在",
        )
        short_path = presets_path().replace(os.path.expanduser("~"), "~")
        self.preset_row = Adw.ActionRow(
            title="自定义预设",
            subtitle=f"{len(USER_PRESETS)} 个｜{short_path}",
        )
        add_preset_btn = Gtk.Button(label="添加…", valign=Gtk.Align.CENTER)
        add_preset_btn.add_css_class("flat")
        add_preset_btn.connect("clicked", self._on_add_preset)
        self.preset_row.add_suffix(add_preset_btn)
        self.preset_row.set_activatable_widget(add_preset_btn)
        preset_group.add(self.preset_row)
        rows_box.append(preset_group)

        # 后端引擎：两个引擎都要 EVIOCGRAB 这只鼠标，只能二选一运行。
        engine_group = Adw.PreferencesGroup(
            title="后端引擎",
            description="决定“长按”在什么时候发出，以及能不能用“双击”",
        )
        self.backend_row = Adw.ComboRow(
            title="引擎",
            subtitle="切换只会改变生成的配置与运行的服务，应用后才生效",
        )
        self.backend_row.set_model(Gtk.StringList.new([label for _n, label in BACKENDS]))
        self.backend_row.set_selected([name for name, _l in BACKENDS].index(self.backend))
        self.backend_row.connect("notify::selected", self._on_backend)
        engine_group.add(self.backend_row)

        self.auto_release_row = Adw.SwitchRow(
            title="长按自动释放",
            subtitle="开：按住到阈值只发一次（实测关掉内核 repeat，按住 1 秒不再等于 24 个回车）；"
                     "关：按住多久就重复多久",
        )
        self.auto_release_row.set_active(self.auto_release)
        self.auto_release_row.connect("notify::active", lambda *_: self._update_preview())
        engine_group.add(self.auto_release_row)

        engine_group.add(Adw.ActionRow(
            title="keyd 没有双击算子",
            subtitle="选 keyd 时“双击”那一栏会被隐藏并从配置里去掉；实测长按比松手早 956ms 发出",
        ))
        rows_box.append(engine_group)

        # 长按阈值：xremap 的 held 对鼠标键只能在松手时判定（鼠标键不发 repeat 事件），
        # 所以能调的只有“按住多久才算长按”。上游默认 1000ms，慢到没人会按住一秒再松手。
        hold_group = Adw.PreferencesGroup(
            title="长按判定", description="长按的动作在松手那一刻发出；按住不足阈值会算成点按"
        )
        self.hold_adjustment = Gtk.Adjustment(
            value=self.hold_timeout,
            lower=HOLD_TIMEOUT_MIN,
            upper=HOLD_TIMEOUT_MAX,
            step_increment=50,
            page_increment=200,
        )
        self.hold_row = Adw.SpinRow(
            title="长按阈值（毫秒）",
            subtitle="上游默认 1000；调小更容易触发长按，但太小时慢一点的点击会被误判",
            adjustment=self.hold_adjustment,
        )
        self.hold_row.connect("notify::value", lambda *_: self._update_preview())
        self.hold_group = hold_group
        hold_group.add(self.hold_row)
        rows_box.append(hold_group)

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
        content.append(self.stale_banner)
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
        self._apply_backend_visibility()
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

    def _preset_problem(self, name, token):
        """返回一句人话说明哪里不行；None 表示可以保存。用的是真校验器，不是自己再猜一套规则。"""
        if not name:
            return "名称不能为空"
        reserved = {lb for lb, _tok in PRESETS} | {NONE_LABEL}
        if name in reserved:
            return "这个名称和内置选项重名了"
        if any(name == n for n, _tok in USER_PRESETS):
            return "同名预设已存在"
        if not token:
            return "写法不能为空（想屏蔽这个键就写 []）"
        if not VALID_TOKEN.match(token):
            return "写法里有不被接受的字符"
        try:
            to_keyd_expr(token)
        except ValueError as exc:
            return str(exc)
        probe = {code: {"tap": None, "hold": None, "double": None} for code, _cn in BUTTONS}
        probe["BTN_SIDE"]["tap"] = token
        try:
            payload = dump_config(XREMAP_BACKEND, probe, None)
        except ValueError as exc:
            return str(exc)
        if not self.validate_fn:
            return None
        tmp = os.path.join(tempfile.gettempdir(), f"sidekeys-preset-{os.getuid()}.yml")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(payload)
            errs = self.validate_fn(tmp)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return errs[0] if errs else None

    def _refresh_preset_choices(self):
        """把新的预设并进每个下拉框，保留当前选择。"""
        for code, _cn in BUTTONS:
            for kind, _gn in GESTURES:
                combo, entry, _old_labels = self.rows[(code, kind)]
                current = combo.get_selected_item().get_string()
                labels = ([NONE_LABEL] if kind in ("hold", "double") else []) + \
                         [lb for lb, _tok in preset_items()]
                combo.set_model(Gtk.StringList.new(labels))
                self.rows[(code, kind)] = (combo, entry, labels)
                combo.set_selected(labels.index(current) if current in labels else labels.index(CUSTOM_LABEL))

    def _on_add_preset(self, _btn):
        dialog = Adw.Window(transient_for=self, modal=True, title="添加预设")
        # 必须留一个引用：GTK4 里没人引用的窗口会被回收，表现为“点了添加什么都没出现”
        self._preset_dialog = dialog
        dialog.set_default_size(460, 300)

        name_row = Adw.EntryRow(title="名称，例如“粘贴并回车”")
        token_row = Adw.EntryRow(title="写法，例如 C-S-V / Enter / F13 / []")
        error_label = Gtk.Label(xalign=0, wrap=True, visible=False)
        error_label.add_css_class("error")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        form = Adw.PreferencesGroup()
        form.add(name_row)
        form.add(token_row)
        content.append(form)
        content.append(error_label)
        save_btn = Gtk.Button(label="保存")
        save_btn.add_css_class("suggested-action")
        save_btn.set_halign(Gtk.Align.END)
        content.append(save_btn)

        def on_save(*_args):
            name = name_row.get_text().strip()
            token = token_row.get_text().strip()
            problem = self._preset_problem(name, token)
            if problem:
                error_label.set_text(problem)
                error_label.set_visible(True)
                return
            USER_PRESETS.append((name, token))
            try:
                save_user_presets()
            except OSError as exc:
                USER_PRESETS.pop()
                error_label.set_text(f"写不进 {presets_path()}：{exc}")
                error_label.set_visible(True)
                return
            rebuild_preset_tables()
            self._refresh_preset_choices()
            self.preset_row.set_subtitle(
                f"{len(USER_PRESETS)} 个｜{presets_path().replace(os.path.expanduser('~'), '~')}")
            self._toast(f"已添加预设：{name}")
            dialog.close()

        save_btn.connect("clicked", on_save)
        token_row.connect("entry-activated", on_save)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(content)
        dialog.set_content(toolbar)
        dialog.present()

    def _on_choice(self, combo, _param, code, kind):
        self._sync_custom_visibility(code, kind)
        self._update_preview()

    def _selected_backend(self):
        label = self.backend_row.get_selected_item().get_string()
        return BACKEND_BY_LABEL.get(label, XREMAP_BACKEND)

    def _on_backend(self, *_):
        self.backend = self._selected_backend()
        self._apply_backend_visibility()
        self._update_preview()
        self._refresh_status()

    def _apply_backend_visibility(self):
        keyd = self.backend == KEYD_BACKEND
        for code, combo, entry in self.double_rows:
            combo.set_visible(not keyd)
            entry.set_visible(False)
            # 自定义输入行的可见性归 _sync_custom_visibility 管，切回来要按原规则恢复
            self._sync_custom_visibility(code, "double")
        self.auto_release_row.set_visible(keyd)
        self.hold_group.set_description(
            "长按的动作在按住达到阈值时立即发出，不等松手（实测比物理松手早 956ms）；不足阈值算点按"
            if keyd else "长按的动作在松手那一刻发出；按住不足阈值会算成点按"
        )

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
            if self.backend == KEYD_BACKEND:
                # keyd 没有双击算子；留着旧值会让生成器直接报错，读起来像界面坏了
                out[code]["double"] = None
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
        try:
            payload = dump_config(self.backend, bindings, self._selected_device(),
                                    self.hold_row.get_value(), self.auto_release_row.get_active())
        except ValueError as exc:
            self.title_widget.set_subtitle(f"写法有误：{exc}")
            return
        self.preview_text.get_buffer().set_text(payload)
        self.title_widget.set_subtitle(self._describe(bindings))

    def _status_unit(self):
        # keyd 后端跑的是它自己的单元；报错了单元就等于在骗用户
        return KEYD_UNIT if self.backend == KEYD_BACKEND else XREMAP_UNIT

    def _other_unit(self):
        return XREMAP_UNIT if self._status_unit() == KEYD_UNIT else KEYD_UNIT

    def _refresh_status(self):
        poll_service(self._on_status)
        return True

    def _on_status(self, states):
        sel, other = self._status_unit(), self._other_unit()
        sel_state = (states.get(sel) or {}).get("ActiveState", "unknown")
        other_state = (states.get(other) or {}).get("ActiveState", "unknown")
        other_backend = KEYD_BACKEND if other == KEYD_UNIT else XREMAP_BACKEND
        banner = self.banner
        if sel_state == "active" and other_state != "active":
            banner.set_revealed(False)
            return
        if sel_state == "active" and other_state == "active":
            # 实测过这个组合的后果：grab 拿到 EBUSY 的一方只是“不选中那个设备”，
            # 服务照样 running，侧键却完全失灵 —— 所以必须显式喊出来
            banner.set_title(f"两个引擎同时在跑（{XREMAP_UNIT} 与 {KEYD_UNIT}）："
                             f"被占用的一方不会报错，只会静默不接管侧键。请点“应用”让本工具强制只留一个")
            banner.set_button_label("立即应用")
            banner.set_property("action-name", "win.apply-current")
            banner.set_revealed(True)
            return
        if other_state == "active":
            banner.set_title(f"当前实际生效的是 {other}（{other_backend} 后端）；"
                             f"你选了 {self.backend}，改动还没应用")
            banner.set_button_label("立即应用")
            banner.set_property("action-name", "win.apply-current")
            banner.set_revealed(True)
            return
        banner.set_title(f"{sel} 未在运行（{sel_state}，历史重启 "
                         f"{(states.get(sel) or {}).get('NRestarts', '?')} 次）")
        banner.set_button_label("启动服务")
        banner.set_property("action-name", "win.start-service")
        banner.set_revealed(True)

    def _check_stale(self):
        if not self.get_visible():
            return False
        current = self_fingerprint()
        stale = self.fingerprint is not None and current is not None and current != self.fingerprint
        self.stale_banner.set_revealed(stale)
        return True

    def _on_restart(self, *_):
        app = self.get_application()
        target = getattr(sys.modules.get("__main__"), "__file__", None) or __file__
        # 必须先退场再拉新进程：app id 是单实例的，旧进程还占着名字时
        # 新执行只会把激活转交给旧窗口，等于没重启。
        app.set_relaunch_target(os.path.realpath(target))
        app.quit()

    def _on_apply(self, _btn):
        if self.stale_banner.get_revealed():
            self._toast("当前窗口是旧版代码，请点横幅上的“重启工具”后再应用", error=True)
            return
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
        if self.backend == KEYD_BACKEND and not helper_supports_keyd():
            self._toast(f"{apply_helper()} 还是旧版，不认识 keyd 后端；请重装（deb 或 sudo ./install.sh）",
                        error=True)
            return
        try:
            payload = dump_config(self.backend, bindings, device,
                                  self.hold_row.get_value(), self.auto_release_row.get_active())
        except ValueError as exc:
            self._toast(str(exc), error=True)
            return
        # 两个后端的不同写法都要能认出“确实绑了某个侧键”，否则误判成空配置会写坏线上映射
        markers = ("BTN_SIDE", "BTN_EXTRA") if self.backend == XREMAP_BACKEND else tuple(KEYD_BUTTON.values())
        if not any(m in payload for m in markers):
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
        self._dispatch(["apply", STAGE_PATH, device, self.backend], "已生效")

    def _dispatch(self, args, ok_message):
        if self.busy:
            return
        self.busy = True
        self.apply_btn.set_sensitive(False)
        self.undo_btn.set_sensitive(False)
        run_in_thread(["pkexec", apply_helper()] + args,
                      lambda rc, out, err: self._done(rc, out, err, ok_message))

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
    bindings, device, hold_timeout, backend, auto_release = read_current()
    if argv[0] == "--print":
        sys.stdout.write(dump_config(backend, bindings, device, hold_timeout, auto_release))
        return 0
    if argv[0] == "--mice":
        print("\n".join(list_mice()))
        return 0
    print("用法: sidekeys [--print | --mice]", file=sys.stderr)
    return 2


class SideKeysApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.andeyunhui.sidekeys")
        self.relaunch_target = None

    def set_relaunch_target(self, path):
        self.relaunch_target = path


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--print", "--mice"):
        return cli_mode(sys.argv[1:])
    load_user_presets()
    rebuild_preset_tables()
    app = SideKeysApp()

    def activate(application):
        # 已开窗口的实例被二次激活时只把它提到前面，不再叠一个窗口
        for existing in application.get_windows():
            existing.present()
            return
        window = SideKeysWindow(application=application)
        window.present()

    app.connect("activate", activate)
    rc = app.run(None)
    if app.relaunch_target:
        subprocess.Popen([sys.executable, app.relaunch_target], close_fds=True)
    return rc


if __name__ == "__main__":
    Adw.init()
    sys.exit(main())
