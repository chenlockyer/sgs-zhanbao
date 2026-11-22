"""腾讯会议自动化：一键开会 → 共享屏幕(仅电脑声音) → 开启录制。

为什么这么做
------------
腾讯会议没有本地可读的转写文件，转写只存在于云端。
要让整条流水线自动化，最可靠的做法是复用它的 UI：
    快速会议 → 共享屏幕 → 共享电脑声音 → 录制(+逐字稿)
录制结束后，逐字稿仍需在客户端/网页导出一次（这是腾讯会议的产品限制，
没有开放给个人号的本地接口）。

依赖与约束
----------
· 需要腾讯会议已登录并运行
· 用 AttachThreadInput 绕过 Windows 前台锁（已验证可行）
· 按钮位置按 3.45.x 版本实测，版本升级后需校准 —— 见 CALIBRATION
· 所有坐标基于 2560x1440 逻辑分辨率、腾讯会议默认窗口大小
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetForegroundWindow.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
kernel32.GetCurrentThreadId.restype = wt.DWORD
user32.GetWindowThreadProcessId.restype = wt.DWORD

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_MOVE = 0x0001
SM_CXSCREEN = 0
SM_CYSCREEN = 1


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("mi", MOUSEINPUT)]


def _screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)


SW_RESTORE = 9
SW_SHOW = 5


def focus_window(hwnd: int) -> bool:
    """绕过 Windows 前台锁把窗口提到最前。

    关键点：
      1. 窗口若被最小化（IsIconic），必须先 SW_RESTORE，否则点不到任何按钮
      2. AttachThreadInput 借用目标线程的输入权，才能突破前台锁
    """
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.45)

    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True

    cur = kernel32.GetCurrentThreadId()
    tgt = user32.GetWindowThreadProcessId(hwnd, None)
    attached = user32.AttachThreadInput(cur, tgt, True)
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.BringWindowToTop(hwnd)
    ok = user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    if attached:
        user32.AttachThreadInput(cur, tgt, False)

    time.sleep(0.5)
    return bool(ok) or user32.GetForegroundWindow() == hwnd


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """返回 (left, top, right, bottom)。"""
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def click_at(x: int, y: int, settle: float = 0.45) -> None:
    """按绝对屏幕坐标点击。"""
    sw, sh = _screen_size()
    ax = int(x * 65535 / sw)
    ay = int(y * 65535 / sh)
    for flags in (
        MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
        MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE,
        MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE,
    ):
        inp = INPUT(type=0, mi=MOUSEINPUT(ax, ay, 0, flags, 0, None))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.06)
    time.sleep(settle)


def find_window_by_pid(pid: int, title_hint: str = "") -> int | None:
    """枚举顶层窗口，找出属于指定进程（可选标题包含）的可见窗口。"""
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if title_hint and title_hint not in title:
            return True
        wpid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(_cb, 0)
    return found[0] if found else None
