#!/usr/bin/env python3
"""sidekeys Windows 版:两个鼠标侧键映射成按键组合。

后端是 WH_MOUSE_LL 低级鼠标钩子:拦下 XBUTTON1/2,用 SendInput 发出组合键。
每个侧键支持三个手势:点按 / 长按(到点即发) / 双击(200ms 窗口)。
纯用户态,不需要驱动和管理员权限。界面是 Tkinter(标准库,零依赖)。
关闭窗口 = 收进托盘继续运行;托盘右键菜单可显示窗口、启停映射、退出。

用法:
    pythonw sidekeys_win.py            # 无控制台窗口
    python  sidekeys_win.py --selftest # 钩子链路自测(不需要操作界面)
"""

import ctypes
import json
import os
import queue
import sys
import threading
import time
from ctypes import wintypes

if sys.platform != "win32":
    sys.exit("这个脚本只能在 Windows 上运行")

import tkinter as tk
from tkinter import messagebox, ttk

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
WM_QUIT = 0x0012
WM_COMMAND = 0x0111
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_TRAY_QUIT = WM_APP + 2
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
HC_ACTION = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
# 这些键在物理键盘上是扩展键(E0 前缀),控制台等应用靠该标志区分小键盘区
EXTENDED_KEYS = {0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28,
                 0x5B, 0x5C, 0x5D, 0x2C, 0x90, 0x6F}
NIM_ADD = 0
NIM_DELETE = 2
NIF_MESSAGE = 1
NIF_ICON = 2
NIF_TIP = 4
ULONG_PTR = wintypes.WPARAM

DOUBLE_MS = 200          # 双击窗口,与 Linux 版一致
HOLD_MS_DEFAULT = 400
HOLD_MS_MIN = 100
HOLD_MS_MAX = 3000

PRESETS = [
    ("复制", "C-c"),
    ("粘贴", "C-v"),
    ("剪切", "C-x"),
    ("复制(终端)", "C-Insert"),
    ("粘贴(终端)", "S-Insert"),
    ("回车", "Enter"),
    ("新开标签页", "C-S-t"),
    ("关闭标签页", "C-w"),
    ("浏览器后退", "A-Left"),
    ("浏览器前进", "A-Right"),
    ("刷新", "F5"),
    ("下一个窗口", "A-Tab"),
    ("上一个窗口", "S-A-Tab"),
    ("锁屏", "M-l"),
    ("屏蔽这个键", "[]"),
    ("不启用(保留原功能)", ""),
    ("自定义…", None),
]
TOKEN_BY_LABEL = dict(PRESETS)
LABEL_BY_TOKEN = {v: k for k, v in PRESETS if v is not None}
CUSTOM_LABEL = PRESETS[-1][0]
BUTTONS = [("xbutton1", "后侧键"), ("xbutton2", "前侧键")]
BOTH = ("both", "双键(一起按)")
ALL_BUTTONS = BUTTONS + [BOTH]
GESTURES = [("tap", "点按"), ("hold", "长按"), ("double", "双击")]
DEFAULTS = {
    "xbutton1": {"tap": "C-c", "hold": None, "double": None},
    "xbutton2": {"tap": "C-v", "hold": None, "double": None},
    "both": {"tap": None, "hold": None, "double": None},
}

MODIFIERS = {
    "c": 0x11, "ctrl": 0x11, "control": 0x11,
    "s": 0x10, "shift": 0x10,
    "a": 0x12, "alt": 0x12,
    "m": 0x5B, "win": 0x5B, "super": 0x5B, "windows": 0x5B,
}
KEYS = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "page_up": 0x21, "page_down": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "pause": 0x13, "capslock": 0x14,
    "minus": 0xBD, "equal": 0xBB, "comma": 0xBC, "dot": 0xBE,
    "slash": 0xBF, "semicolon": 0xBA, "quote": 0xDE,
    "leftbrace": 0xDB, "rightbrace": 0xDD, "backslash": 0xDC, "grave": 0xC0,
}
for _c in "abcdefghijklmnopqrstuvwxyz0123456789":
    KEYS[_c] = ord(_c.upper())
for _i in range(1, 25):
    KEYS[f"f{_i}"] = 0x6F + _i


def config_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "sidekeys", "config.json")


def parse_combo(text):
    """'C-s-t' -> [0x11, 0x10, 0x54];空串/None -> None(不启用);'[]' -> [](屏蔽)。"""
    if text is None or not str(text).strip():
        return None
    text = str(text).strip()
    if text == "[]":
        return []
    parts = [p for p in text.split("-") if p]
    if not parts:
        raise ValueError(f"无法解析:{text!r}")
    vks = []
    for mod in parts[:-1]:
        vk = MODIFIERS.get(mod.lower())
        if vk is None:
            raise ValueError(f"{text!r} 里的 {mod!r} 不是修饰键(可用 C/S/A/M,即 Ctrl/Shift/Alt/Win)")
        vks.append(vk)
    key = parts[-1].lower()
    vk = KEYS.get(key)
    if vk is None:
        raise ValueError(f"不认识的键名:{parts[-1]!r}(举例:c、v、Enter、F5、Left)")
    vks.append(vk)
    return vks


# ---------------------------------------------------------------- Win32 结构

class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


HOOKPROC = ctypes.CFUNCTYPE(ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
WNDPROC = ctypes.CFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                           wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
                ("hBalloonIcon", wintypes.HICON)]


user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.CallNextHookEx.restype = ctypes.c_longlong
user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ULONG_PTR]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.LoadIconW.restype = wintypes.HICON
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]


def send_combo(vks):
    seq = []
    for vk in vks:
        flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_KEYS else 0
        seq.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, dwFlags=flags)))
    for vk in reversed(vks):
        flags = KEYEVENTF_KEYUP | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_KEYS else 0)
        seq.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, dwFlags=flags)))
    arr = (INPUT * len(seq))(*seq)
    sent = user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))
    if sent != len(seq):
        raise ctypes.WinError(ctypes.get_last_error())


class MouseHook:
    """独立线程装钩子跑消息泵。手势状态机:
    按下 → 有点按以外手势时起长按定时器;到点即发长按(与 keyd 语义一致)。
    松手 → 没发过长按:绑了双击就等 200ms 窗口,第二次按下即发双击;否则立即发点按。
    mapping 整个替换即热更新,无需重装钩子。
    """

    def __init__(self, mapping, hold_ms=HOLD_MS_DEFAULT):
        self.mapping = mapping
        self.hold_ms = hold_ms
        self._states = {}
        self._phys = set()          # 当前物理按下的侧键
        self._chord_active = False
        self._chord_members = set()  # 被本次和弦占用、松手时不触发单键动作的键
        self._lock = threading.Lock()
        self._thread = None
        self._tid = None
        self._ready = threading.Event()
        self._error = None
        self._proc = None  # 防止回调被 GC

    def set_mapping(self, mapping, hold_ms):
        with self._lock:
            self._cancel_timers()
            self._states = {}
            self._phys = set()
            self._chord_active = False
            self._chord_members = set()
            self.mapping = mapping
            self.hold_ms = hold_ms

    def _cancel_timers(self):
        for st in self._states.values():
            for name in ("hold_timer", "tap_timer"):
                timer = st.get(name)
                if timer:
                    timer.cancel()

    @staticmethod
    def _fire(vks):
        if vks:
            try:
                send_combo(vks)
            except OSError:
                pass  # 注入失败(如会话锁定)时 swallow,钩子不能崩

    def _hold_fire(self, key):
        with self._lock:
            self._states.setdefault(key, {})["hold_fired"] = True
            self._fire((self.mapping.get(key) or {}).get("hold"))

    def _tap_fire(self, key):
        with self._lock:
            st = self._states.get(key, {})
            if st.get("awaiting_double"):
                st["awaiting_double"] = False
                self._fire((self.mapping.get(key) or {}).get("tap"))

    def _down(self, key, gest):
        st = self._states.setdefault(key, {})
        if st.get("awaiting_double"):
            st["awaiting_double"] = False
            st["tap_timer"].cancel()
            st["double_fired"] = True
            self._fire(gest.get("double"))
            return
        st["hold_fired"] = False
        st["double_fired"] = False
        if gest.get("hold") is not None:
            timer = threading.Timer(self.hold_ms / 1000, self._hold_fire, args=(key,))
            timer.daemon = True
            st["hold_timer"] = timer
            timer.start()

    def _up(self, key, gest):
        st = self._states.setdefault(key, {})
        if st.get("double_fired"):
            st["double_fired"] = False
            return
        timer = st.get("hold_timer")
        if timer:
            timer.cancel()
        if st.get("hold_fired"):
            st["hold_fired"] = False
            return
        if gest.get("double") is not None:
            st["awaiting_double"] = True
            timer = threading.Timer(DOUBLE_MS / 1000, self._tap_fire, args=(key,))
            timer.daemon = True
            st["tap_timer"] = timer
            timer.start()
        else:
            self._fire(gest.get("tap"))

    @staticmethod
    def _has_gesture(gest):
        return bool(gest) and any(gest.get(g) is not None for g, _ in GESTURES)

    def _reset(self, key):
        st = self._states.get(key)
        if st:
            for name in ("hold_timer", "tap_timer"):
                timer = st.get(name)
                if timer:
                    timer.cancel()
            st.clear()

    def _phys_down(self, button, gest_ind, gest_both):
        self._phys.add(button)
        if button in self._chord_members:
            return
        if len(self._phys) == 2 and self._has_gesture(gest_both):
            # 两键齐按:切到双键通道,取消两个单键的待发动作
            self._chord_active = True
            self._chord_members = {1, 2}
            self._reset("xbutton1")
            self._reset("xbutton2")
            self._down("both", gest_both)
        elif not self._chord_active:
            self._down(f"xbutton{button}", gest_ind)

    def _phys_up(self, button, gest_ind, gest_both):
        self._phys.discard(button)
        if button in self._chord_members:
            self._chord_members.discard(button)
            if self._chord_active:
                self._chord_active = False
                self._up("both", gest_both)
            return
        if not self._chord_active:
            self._up(f"xbutton{button}", gest_ind)

    def _run(self):
        def callback(nCode, wParam, lParam):
            if nCode == HC_ACTION and wParam in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                button = (info.mouseData >> 16) & 0xFFFF
                if button in (1, 2):
                    gest_ind = self.mapping.get(f"xbutton{button}") or {}
                    gest_both = self.mapping.get("both") or {}
                    # 绑了和弦时两个单键都被"占用"(即使单键没绑),否则一起按会先漏出单键原功能
                    if self._has_gesture(gest_ind) or self._has_gesture(gest_both):
                        with self._lock:
                            if wParam == WM_XBUTTONDOWN:
                                self._phys_down(button, gest_ind, gest_both)
                            else:
                                self._phys_up(button, gest_ind, gest_both)
                        return 1  # down/up 都吞掉,否则目标应用会看到半个点击
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._proc = HOOKPROC(callback)
        handle = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc,
                                          kernel32.GetModuleHandleW(None), 0)
        if not handle:
            self._error = ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            return
        self._tid = kernel32.GetCurrentThreadId()
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(handle)

    def start(self, timeout=3):
        self._thread = threading.Thread(target=self._run, daemon=True, name="sidekeys-hook")
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("钩子线程没有按时就绪")
        if self._error:
            raise self._error

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(timeout=3)
        self._tid = None
        with self._lock:
            self._cancel_timers()
            self._states = {}
            self._phys = set()
            self._chord_active = False
            self._chord_members = set()


class TrayIcon:
    """通知栏图标 + 右键菜单。隐藏窗口跑消息泵;菜单命令放进队列,由 Tk 主线程取走
    (Tkinter 不是线程安全的,不能在托盘线程里直接动界面)。"""

    MENU_SHOW = 1
    MENU_TOGGLE = 2
    MENU_QUIT = 3
    MENU_ELEVATE = 4

    def __init__(self, tip, command_queue, is_running, can_elevate=False):
        self.command_queue = command_queue
        self.is_running = is_running
        self.can_elevate = can_elevate
        self.tip = tip
        self._hwnd = None
        self._thread = None
        self._ready = threading.Event()
        self._error = None
        self._wndproc = None
        self._nid = None

    def _show_menu(self):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, self.MENU_SHOW, "显示主窗口")
        user32.AppendMenuW(menu, 0, self.MENU_TOGGLE,
                           "停止映射" if self.is_running() else "启用映射")
        if self.can_elevate:
            user32.AppendMenuW(menu, 0, self.MENU_ELEVATE, "以管理员身份重启(管理员终端需要)")
        user32.AppendMenuW(menu, 0, self.MENU_QUIT, "退出")
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self._hwnd)
        user32.TrackPopupMenu(menu, 0x0002, pt.x, pt.y, 0, self._hwnd, None)  # TPM_RIGHTBUTTON
        user32.PostMessageW(self._hwnd, 0, 0, 0)  # WM_NULL,MSDN 要求的收尾
        user32.DestroyMenu(menu)

    def _run(self):
        def wndproc(hwnd, msg, wParam, lParam):
            if msg == WM_TRAY:
                if lParam == WM_RBUTTONUP:
                    self._show_menu()
                elif lParam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self.command_queue.put("show")
                return 0
            if msg == WM_COMMAND:
                cmd = {self.MENU_SHOW: "show", self.MENU_TOGGLE: "toggle",
                       self.MENU_QUIT: "quit", self.MENU_ELEVATE: "elevate"}.get(wParam & 0xFFFF)
                if cmd:
                    self.command_queue.put(cmd)
                return 0
            if msg == WM_TRAY_QUIT:
                shell32.Shell_NotifyIconW(NIM_DELETE, self._nid)
                user32.DestroyWindow(hwnd)
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wParam, lParam)

        self._wndproc = WNDPROC(wndproc)
        hinstance = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._wndproc,
                          hInstance=hinstance, lpszClassName=f"sidekeys-tray-{os.getpid()}")
        if not user32.RegisterClassExW(ctypes.byref(cls)):
            self._error = ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            return
        self._hwnd = user32.CreateWindowExW(0, cls.lpszClassName, "sidekeys-tray", 0,
                                            0, 0, 0, 0, None, None, hinstance, None)
        if not self._hwnd:
            self._error = ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            return
        self._nid = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self._hwnd,
                                    uID=1, uFlags=NIF_MESSAGE | NIF_ICON | NIF_TIP,
                                    uCallbackMessage=WM_TRAY,
                                    hIcon=user32.LoadIconW(None, wintypes.LPCWSTR(32512)),
                                    szTip=self.tip)
        if not shell32.Shell_NotifyIconW(NIM_ADD, self._nid):
            self._error = ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def start(self, timeout=3):
        self._thread = threading.Thread(target=self._run, daemon=True, name="sidekeys-tray")
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("托盘线程没有按时就绪")
        if self._error:
            raise self._error

    def remove(self):
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_TRAY_QUIT, 0, 0)
            self._thread.join(timeout=3)
            self._hwnd = None


# ---------------------------------------------------------------- 配置读写

def load_config():
    tokens = {k: dict(v) for k, v in DEFAULTS.items()}
    hold_ms = HOLD_MS_DEFAULT
    try:
        with open(config_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            ms = data.get("hold_ms")
            if isinstance(ms, int) and HOLD_MS_MIN <= ms <= HOLD_MS_MAX:
                hold_ms = ms
            for key, _ in ALL_BUTTONS:
                val = data.get(key)
                if isinstance(val, str):  # 旧版只存点按
                    val = {"tap": val}
                if isinstance(val, dict):
                    for g, _ in GESTURES:
                        if isinstance(val.get(g), str):
                            tokens[key][g] = val[g]
    except (OSError, ValueError):
        pass
    return tokens, hold_ms


def save_config(tokens, hold_ms):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"hold_ms": hold_ms, **tokens}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 开机启动

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "sidekeys"


def set_autostart(enable):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enable:
            if getattr(sys, "frozen", False):  # PyInstaller 打包后 exe 即全部,不能再带脚本路径
                cmd = f'"{sys.executable}"'
            else:
                script = os.path.abspath(__file__)
                pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                exe = pythonw if os.path.exists(pythonw) else sys.executable
                cmd = f'"{exe}" "{script}"'
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


def is_elevated():
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        return False
    try:
        elevation = wintypes.DWORD()
        ret = wintypes.DWORD()
        if not advapi32.GetTokenInformation(token, 20, ctypes.byref(elevation), 4, ctypes.byref(ret)):
            return False
        return bool(elevation.value)
    finally:
        kernel32.CloseHandle(token)


def restart_elevated():
    """以管理员身份重启自己。成功返回 True;用户取消 UAC 或失败返回 False。"""
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, None
    else:
        script = os.path.abspath(__file__)
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pythonw if os.path.exists(pythonw) else sys.executable
        params = f'"{script}"'
    shell32.ShellExecuteW.restype = wintypes.HINSTANCE
    shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                      wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    return int(shell32.ShellExecuteW(None, "runas", exe, params, None, 1)) > 32


# ---------------------------------------------------------------- GUI

class App:
    def __init__(self, root):
        self.root = root
        self.hook = None
        self.tray = None
        self.tray_commands = queue.Queue()
        root.title("sidekeys (Windows)")
        root.resizable(False, False)
        main = ttk.Frame(root, padding=12)
        main.grid(sticky="nsew")

        tokens, hold_ms = load_config()
        self.rows = {}
        for i, (key, label) in enumerate(ALL_BUTTONS):
            frame = ttk.LabelFrame(main, text=label, padding=6)
            frame.grid(row=i, column=0, columnspan=3, sticky="w", pady=4)
            for j, (gesture, glabel) in enumerate(GESTURES):
                ttk.Label(frame, text=glabel).grid(row=j, column=0, sticky="w", pady=2)
                combo = ttk.Combobox(frame, state="readonly", width=22,
                                     values=[p[0] for p in PRESETS])
                combo.grid(row=j, column=1, padx=6)
                entry = ttk.Entry(frame, width=14)
                entry.grid(row=j, column=2)
                token = tokens[key][gesture]
                combo.set(LABEL_BY_TOKEN.get(token or "", CUSTOM_LABEL))
                entry.insert(0, token or "")
                if combo.get() != CUSTOM_LABEL:
                    entry.configure(state="disabled")
                combo.bind("<<ComboboxSelected>>",
                           lambda _e, c=combo, en=entry: self._preset_picked(c, en))
                self.rows[(key, gesture)] = (combo, entry)

        row = len(ALL_BUTTONS)
        ttk.Label(main, text="长按阈值(ms)").grid(row=row, column=0, sticky="w", pady=4)
        self.hold_ms = tk.IntVar(value=hold_ms)
        ttk.Spinbox(main, from_=HOLD_MS_MIN, to=HOLD_MS_MAX, increment=50,
                    textvariable=self.hold_ms, width=8).grid(row=row, column=1, sticky="w", padx=6)

        bar = ttk.Frame(main)
        bar.grid(row=row + 1, column=0, columnspan=3, pady=(10, 4), sticky="w")
        ttk.Button(bar, text="应用", command=self.apply).pack(side="left")
        ttk.Button(bar, text="停止", command=self.stop).pack(side="left", padx=6)
        self.auto_var = tk.BooleanVar(value=autostart_enabled())
        ttk.Checkbutton(bar, text="开机启动", variable=self.auto_var,
                        command=self._toggle_autostart).pack(side="left", padx=6)

        self.status = ttk.Label(main, text="未运行", foreground="gray")
        self.status.grid(row=row + 2, column=0, columnspan=3, sticky="w")
        hint = "提示:关闭窗口收进托盘,映射继续;托盘右键菜单可退出。"
        if not is_elevated():
            hint += "\n管理员身份的终端不生效时,请用托盘菜单「以管理员身份重启」。"
        ttk.Label(main, foreground="gray", text=hint).grid(
            row=row + 3, column=0, columnspan=3, sticky="w")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        try:
            self.tray = TrayIcon("sidekeys 侧键映射", self.tray_commands,
                                 lambda: self.hook is not None,
                                 can_elevate=not is_elevated())
            self.tray.start()
        except OSError as exc:
            self.tray = None
            messagebox.showwarning("托盘不可用", f"托盘图标创建失败,关闭窗口将直接退出:{exc}")
        root.after(150, self._poll_tray)

        if os.path.exists(config_path()):
            self.apply()  # 有保存的配置就直接生效,开机启动时不需要手点

    def _preset_picked(self, combo, entry):
        token = TOKEN_BY_LABEL[combo.get()]
        entry.configure(state="normal")
        entry.delete(0, "end")
        if token is None:
            entry.focus_set()
        else:
            entry.insert(0, token)
            entry.configure(state="disabled")

    def _read_tokens(self):
        tokens = {k: {} for k, _ in ALL_BUTTONS}
        for (key, gesture), (combo, entry) in self.rows.items():
            if combo.get() == CUSTOM_LABEL:
                tokens[key][gesture] = entry.get().strip() or None
            else:
                tokens[key][gesture] = TOKEN_BY_LABEL[combo.get()] or None
        return tokens

    def apply(self):
        tokens = self._read_tokens()
        try:
            hold_ms = int(self.hold_ms.get())
            if not HOLD_MS_MIN <= hold_ms <= HOLD_MS_MAX:
                raise ValueError(f"长按阈值需在 {HOLD_MS_MIN}~{HOLD_MS_MAX}ms 之间")
            mapping = {k: {g: parse_combo(t) for g, t in gestures.items()}
                       for k, gestures in tokens.items()}
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("写法有误", str(exc))
            return
        try:
            if self.hook:
                self.hook.set_mapping(mapping, hold_ms)  # 热更新,钩子不动
            else:
                self.hook = MouseHook(mapping, hold_ms)
                self.hook.start()
            save_config(tokens, hold_ms)
        except OSError as exc:
            self.hook = None
            messagebox.showerror("启动失败", f"装钩子或写配置失败:{exc}")
            return
        self.status.configure(text="运行中:侧键已映射", foreground="green")

    def stop(self):
        if self.hook:
            self.hook.stop()
            self.hook = None
        self.status.configure(text="已停止:侧键恢复原功能", foreground="gray")

    def _toggle_autostart(self):
        try:
            set_autostart(self.auto_var.get())
        except OSError as exc:
            messagebox.showerror("开机启动设置失败", str(exc))
            self.auto_var.set(autostart_enabled())

    def _poll_tray(self):
        try:
            while True:
                cmd = self.tray_commands.get_nowait()
                if cmd == "show":
                    self.root.deiconify()
                    self.root.lift()
                elif cmd == "toggle":
                    if self.hook:
                        self.stop()
                    else:
                        self.apply()
                elif cmd == "elevate":
                    if restart_elevated():
                        self._quit_real()
                        return
                elif cmd == "quit":
                    self._quit_real()
                    return
        except queue.Empty:
            pass
        self.root.after(150, self._poll_tray)

    def _on_close(self):
        if self.tray:
            self.root.withdraw()
        else:
            self._quit_real()

    def _quit_real(self):
        self.stop()
        if self.tray:
            self.tray.remove()
        self.root.destroy()


# ---------------------------------------------------------------- 自测

def selftest():
    ok = True
    # 解析器正负例
    assert parse_combo("C-c") == [0x11, 0x43]
    assert parse_combo("C-S-t") == [0x11, 0x10, 0x54]
    assert parse_combo("[]") == []
    assert parse_combo("") is None
    for bad in ("X-c", "C-notakey", "c-v-x"):
        try:
            parse_combo(bad)
        except ValueError:
            pass
        else:
            print(f"FAIL: {bad!r} 应当报错却通过了")
            ok = False

    # 端到端:鼠标钩子把注入的侧键手势翻译成按键,键盘钩子验证收到了它。
    # 用 F13-F15(0x7C-0x7E),注入到任何窗口都几乎无副作用。
    captured = []

    def kb_callback(nCode, wParam, lParam):
        if nCode == HC_ACTION and wParam in (0x0100, 0x0104):  # KEYDOWN / SYSKEYDOWN
            info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            captured.append(info.vkCode)
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    kb_proc = HOOKPROC(kb_callback)
    kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kb_proc,
                                       kernel32.GetModuleHandleW(None), 0)
    if not kb_hook:
        print("FAIL: 键盘钩子装不上", ctypes.WinError(ctypes.get_last_error()))
        return 1

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            msg = wintypes.MSG()
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)

    def click():
        user32.mouse_event(0x0080, 0, 0, 0x0001, 0)  # XBUTTONDOWN, XBUTTON1
        user32.mouse_event(0x0100, 0, 0, 0x0001, 0)  # XBUTTONUP

    def gestures(tap=None, hold=None, double=None):
        return {"xbutton1": {g: parse_combo(v) for g, v in
                             (("tap", tap), ("hold", hold), ("double", double))}}

    def run_case(name, mapping, hold_ms, actions, expected, forbidden=()):
        nonlocal ok
        captured.clear()
        hook = MouseHook(mapping, hold_ms)
        try:
            hook.start()
        except OSError as exc:
            print(f"FAIL: {name}: 鼠标钩子装不上 {exc}")
            ok = False
            return
        pump(0.05)
        actions()
        pump(0.5)  # 覆盖双击窗口和长按判定的等待
        hook.stop()
        missing = [hex(v) for v in expected if v not in captured]
        leaked = [hex(v) for v in forbidden if v in captured]
        if missing or leaked:
            print(f"FAIL: {name}: 缺 {missing} 不该有 {leaked} (captured={[hex(v) for v in captured]})")
            ok = False
        else:
            print(f"PASS: {name}")

    run_case("点按(无双击绑定时立即发)", gestures(tap="f13"), 400, click, [0x7C])

    def double_click():
        click()
        pump(0.05)
        click()

    run_case("双击(200ms 内第二击)", gestures(tap="f13", double="f14"), 400,
             double_click, [0x7D], forbidden=[0x7C])

    def hold_press():
        user32.mouse_event(0x0080, 0, 0, 0x0001, 0)
        pump(0.25)  # 超过 150ms 阈值,长按应在松手前已发
        user32.mouse_event(0x0100, 0, 0, 0x0001, 0)

    run_case("长按到点即发(松手前)", gestures(tap="f13", hold="f15"), 150,
             hold_press, [0x7E], forbidden=[0x7C])
    run_case("全部不启用则放行(不发任何键)", gestures(), 400, click, [])

    def g(tap=None, hold=None, double=None):
        return {k: parse_combo(v) for k, v in
                (("tap", tap), ("hold", hold), ("double", double))}

    def both_down():
        user32.mouse_event(0x0080, 0, 0, 1, 0)
        user32.mouse_event(0x0080, 0, 0, 2, 0)

    def both_up():
        user32.mouse_event(0x0100, 0, 0, 1, 0)
        user32.mouse_event(0x0100, 0, 0, 2, 0)

    def both_click():
        both_down()
        both_up()

    run_case("和弦点按(不触发单键)", {"xbutton1": g(tap="f13"), "xbutton2": g(tap="f13"),
                                     "both": g(tap="f16")}, 400, both_click,
             [0x7F], forbidden=[0x7C])

    def both_hold():
        both_down()
        pump(0.25)
        both_up()

    run_case("和弦长按到点即发", {"xbutton1": g(tap="f13"), "both": g(hold="f17")}, 150,
             both_hold, [0x80], forbidden=[0x7C])

    def both_double():
        both_click()
        pump(0.05)
        both_click()

    run_case("和弦双击", {"both": g(tap="f16", double="f18")}, 400, both_double,
             [0x81], forbidden=[0x7F])
    run_case("绑了和弦单键仍独立", {"xbutton1": g(tap="f13"), "both": g(tap="f16")}, 400,
             click, [0x7C], forbidden=[0x7F])

    user32.UnhookWindowsHookEx(kb_hook)
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        return selftest()
    # 管理员终端需要高权限注入(UIPI)。普通权限启动时自动提权重启一次;
    # 本机 UAC 静默批准则无感,标准 UAC 机器会弹一次框,取消则退回普通权限运行。
    if not is_elevated() and "--no-elevate" not in sys.argv:
        if restart_elevated():
            return 0
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())