"""腾讯会议一键开录（快速会议 → 共享屏幕 → 仅电脑声音 → 开始共享）。

坐标策略
--------
腾讯会议是 Qt 应用，UIA 树拿不到按钮，只能按像素点。
为抗窗口移动/缩放，这里用 **窗口内相对坐标**（0~1 归一化），
运行时按当前窗口矩形换算成屏幕坐标。

校准怎么做
----------
运行  python -m sgs_zhanbao.wemeet_auto --calibrate
会把腾讯会议窗口截图 + 绿色坐标网格存到 output/wemeet/，
照着图确认每个按钮的位置百分比，填到 STEPS 里即可。

安全设计
--------
· 默认 dry-run，只报告每一步会点哪里，不真的点
· --go 才执行，且每步之后截图存档，便于回溯
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import subprocess
import time
from pathlib import Path

from .config import output_dir
from .wemeet import (
    click_at,
    find_window_by_pid,
    focus_window,
    get_window_rect,
)


def _out_dir() -> Path:
    d = output_dir() / "wemeet"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 截图

def screenshot(tag: str) -> Path:
    """Win32 GDI 抓全屏（不起子进程，沙箱下也能用）。"""
    from PIL import Image

    user32 = ctypes.WinDLL("user32")
    gdi32 = ctypes.WinDLL("gdi32")
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)

    hdc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)

    class BMPH(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
            ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
            ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
        ]

    bi = BMPH()
    bi.biSize = ctypes.sizeof(BMPH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32

    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, hdc)

    p = _out_dir() / f"{time.strftime('%H%M%S')}_{tag}.png"
    img.save(p)
    return p


# ---------------------------------------------------------------- 进程/窗口

def wemeet_pids() -> list[int]:
    r = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq wemeetapp.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, encoding="gbk", errors="ignore",
    )
    pids = []
    for line in r.stdout.splitlines():
        parts = [x.strip() for x in line.split('","')]
        if len(parts) > 1 and parts[1].strip('"').isdigit():
            pids.append(int(parts[1].strip('"')))
    return pids


def main_window() -> int | None:
    for pid in wemeet_pids():
        h = find_window_by_pid(pid, "腾讯会议")
        if h:
            return h
    return None


# ---------------------------------------------------------------- 流程定义

# 归一化坐标 (rx, ry)：相对窗口宽高的比例
# 实测自 3.45.10.411，窗口 1492x884
STEPS: list[tuple[str, float, float, float]] = [
    # (说明, rx, ry, 等待秒数)
    ("打开「快速会议」", 300 / 1492, 244 / 884, 6.0),
    # 会中窗口布局与会前不同，下列坐标需在会中窗口下校准
    ("点击下方「共享屏幕」", 0.315, 0.925, 2.5),
    ("选择「仅电脑声音」", 0.505, 0.478, 1.2),
    ("点击「开始共享」", 0.783, 0.855, 3.0),
]


def run_flow(go: bool = False) -> int:
    hwnd = main_window()
    if not hwnd:
        print("✗ 未找到腾讯会议窗口，请先启动并登录")
        return 1

    print(f"✓ 窗口 hwnd={hwnd}")
    if not focus_window(hwnd):
        print("⚠ 前置失败，点击可能落到其他窗口上，已中止")
        return 1

    left, top, right, bottom = get_window_rect(hwnd)
    w, h = right - left, bottom - top
    print(f"  窗口矩形 ({left},{top})-({right},{bottom})  {w}x{h}")

    for i, (desc, rx, ry, wait) in enumerate(STEPS, 1):
        x = left + int(rx * w)
        y = top + int(ry * h)
        print(f"[{i}/{len(STEPS)}] {desc}  → 屏幕 ({x},{y})")
        if not go:
            continue
        click_at(x, y, settle=wait)
        shot = screenshot(f"step{i}")
        print(f"       已截图 {shot.name}")

    if go:
        print("\n✅ 执行完毕。请确认是否已在共享电脑声音、并已开始录制。")
    else:
        print("\n（dry-run，未点击。加 --go 执行）")
    return 0


def calibrate() -> int:
    hwnd = main_window()
    if not hwnd:
        print("✗ 未找到腾讯会议窗口")
        return 1
    if not focus_window(hwnd):
        print("⚠ 前置失败")
        return 1
    time.sleep(1.0)

    left, top, right, bottom = get_window_rect(hwnd)
    print(f"窗口矩形 ({left},{top})-({right},{bottom})")

    p = screenshot("calibrate")
    print(f"截图：{p}")

    # 在窗口区域叠坐标网格，方便读百分比
    from PIL import Image, ImageDraw

    im = Image.open(p).convert("RGB").crop((left, top, right, bottom))
    d = ImageDraw.Draw(im)
    for x in range(0, im.width, 50):
        d.line([(x, 0), (x, im.height)], fill=(0, 255, 0), width=1)
    for y in range(0, im.height, 50):
        d.line([(0, y), (im.width, y)], fill=(0, 255, 0), width=1)
    gp = _out_dir() / "grid_window.png"
    im.save(gp)
    print(f"网格图：{gp}   窗口尺寸 {im.size}")
    print("按网格读出按钮像素坐标，除以窗口宽高得到 rx, ry，填进 STEPS。")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="腾讯会议自动开录")
    ap.add_argument("--calibrate", action="store_true", help="截图+网格，用于校准坐标")
    ap.add_argument("--go", action="store_true", help="真正执行点击")
    a = ap.parse_args()
    raise SystemExit(calibrate() if a.calibrate else run_flow(a.go))
