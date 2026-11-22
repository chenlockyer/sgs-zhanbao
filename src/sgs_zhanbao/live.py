"""直播工作流编排：开播检测 → 录制提醒 → 转写稿处理 → 出图。

设计原则：把「必须人工」的步骤明确说清楚，不硬做不可靠的自动化。
"""

from __future__ import annotations

import argparse
import time
import webbrowser

from .bilibili import DEFAULT_ROOM, fetch_room_info, wait_until_live
from .config import ensure_dirs, transcript_dir


def cmd_live(args) -> int:
    """打开直播间并（可选）等待开播。"""
    url = f"https://live.bilibili.com/{args.room}"
    info = fetch_room_info(args.room)
    print(f"直播间 {info.room_id}：{info.status_text}")
    if info.title:
        print(f"标题：{info.title}")

    if args.open:
        print(f"打开 {url}")
        webbrowser.open(url)

    if not args.wait:
        return 0
    if info.is_living:
        print("✓ 已经在直播中")
        return 0
    print(f"等待开播（每 {args.interval}s 检查一次）…")
    if wait_until_live(args.room, timeout_s=args.timeout, interval_s=args.interval):
        print("✓ 开播了")
        return 0
    print("✗ 超时仍未开播")
    return 1


def cmd_prep(args) -> int:
    """打印赛前准备清单。"""
    ensure_dirs()
    info = fetch_room_info(args.room)
    print("=" * 56)
    print("  赛前准备")
    print("=" * 56)
    print(f"\n直播间  https://live.bilibili.com/{args.room}   [{info.status_text}]")
    if info.title:
        print(f"        {info.title}")

    print("\n腾讯会议里按顺序做：")
    print("  1. 点「快速会议」")
    print("  2. 底部「共享屏幕」")
    print("  3. 选「仅电脑声音」")
    print("  4. 点「开始共享」")
    print("  5. 底部「录制」开始录制；右侧确认「逐字稿」已开")

    print("\n赛后：")
    print("  6. 腾讯会议 → 转写 → 导出文本")
    print(f"  7. 把 txt 放进  {transcript_dir()}")
    print("  8. sgs-zhanbao run --transcript voice2text/xxx.txt")
    print()
    return 0


def cmd_watch(args) -> int:  # noqa: ARG001 —— argparse 统一签名
    """盯住 voice2text 目录，发现新稿子就提示。"""
    ensure_dirs()
    d = transcript_dir()
    seen = {p.name for p in d.glob("*.txt")}
    print(f"监视 {d}（已有 {len(seen)} 个稿子）…  Ctrl+C 退出")
    try:
        while True:
            now = {p.name for p in d.glob("*.txt")}
            for name in sorted(now - seen):
                p = d / name
                # 等文件大小稳定，避免读到半个文件
                last, stable = -1, 0
                for _ in range(40):
                    size = p.stat().st_size
                    if size == last:
                        stable += 1
                        if stable >= 2:
                            break
                    else:
                        stable = 0
                    last = size
                    time.sleep(0.5)
                print(f"\n发现新转写稿：{name}  ({last / 1024:.0f} KB)")
                print(f"跑：sgs-zhanbao run --transcript voice2text/{name}")
            seen = now
            time.sleep(3)
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="sgs-zhanbao-live", description="直播工作流编排")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("live", help="打开直播间 / 等待开播")
    pl.add_argument("--room", type=int, default=DEFAULT_ROOM)
    pl.add_argument("--open", action="store_true", help="用浏览器打开")
    pl.add_argument("--wait", action="store_true", help="等待开播")
    pl.add_argument("--interval", type=int, default=60)
    pl.add_argument("--timeout", type=int, default=7200)
    pl.set_defaults(func=cmd_live)

    pp = sub.add_parser("prep", help="打印赛前准备清单")
    pp.add_argument("--room", type=int, default=DEFAULT_ROOM)
    pp.set_defaults(func=cmd_prep)

    sub.add_parser("watch", help="监视新转写稿").set_defaults(func=cmd_watch)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
