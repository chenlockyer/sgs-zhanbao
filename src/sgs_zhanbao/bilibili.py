"""B 站直播间：状态检测 + 打开页面。

用途：开赛前确认直播间是否已开播。不抓流、不录屏 —— 音频由系统环回采集
（见 :mod:`sgs_zhanbao.record`）或腾讯会议「共享电脑声音」捕获。
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable

# 三国杀官方赛事直播间（可在 CLI / 代码里覆盖）
DEFAULT_ROOM = 1327597

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

STATUS_TEXT = {0: "未开播", 1: "直播中", 2: "轮播/回放"}


@dataclass
class RoomInfo:
    room_id: int
    live_status: int          # 0=未开播 1=直播中 2=轮播 -1=查询失败
    title: str = ""
    anchor: str = ""
    online: int = 0

    @property
    def is_living(self) -> bool:
        return self.live_status == 1

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.live_status, "未知")


def _get(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Referer": "https://live.bilibili.com/"}
    )
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def fetch_room_info(room_id: int = DEFAULT_ROOM) -> RoomInfo:
    """查询直播间状态（失败时返回 live_status=-1，不抛异常）。"""
    url = f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_id}"
    try:
        data = _get(url)
        d = data.get("data") or {}
        if not d:
            return RoomInfo(room_id=room_id, live_status=-1)
        return RoomInfo(
            room_id=int(d.get("room_id", room_id)),
            live_status=int(d.get("live_status", 0)),
            title=d.get("title", ""),
            anchor=str(d.get("uid", "")),
            online=int(d.get("online", 0)),
        )
    except Exception as exc:  # noqa: BLE001 —— 查询失败不该中断流程
        return RoomInfo(room_id=room_id, live_status=-1, title=f"查询失败：{exc}")


def wait_until_live(
    room_id: int = DEFAULT_ROOM,
    *,
    timeout_s: int = 3600,
    interval_s: int = 60,
    log: Callable[[str], None] = print,
) -> bool:
    """轮询直到开播。返回是否检测到直播。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        info = fetch_room_info(room_id)
        log(
            f"[{time.strftime('%H:%M:%S')}] 房间 {info.room_id} "
            f"状态={info.status_text} {info.title[:40]}"
        )
        if info.is_living:
            return True
        time.sleep(interval_s)
    return False


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="查询 B 站直播间状态")
    ap.add_argument("--room", type=int, default=DEFAULT_ROOM)
    ap.add_argument("--wait", action="store_true", help="轮询等待开播")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--timeout", type=int, default=7200)
    a = ap.parse_args(argv)

    info = fetch_room_info(a.room)
    print(f"直播间 {info.room_id}：{info.status_text}")
    if info.title:
        print(f"标题：{info.title}")
    if not a.wait:
        return 0
    if info.is_living:
        print("✓ 已在直播中")
        return 0
    print(f"等待开播（每 {a.interval}s 检查一次，最多 {a.timeout}s）…")
    if wait_until_live(a.room, timeout_s=a.timeout, interval_s=a.interval):
        print("✓ 开播了")
        return 0
    print("✗ 超时仍未开播")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
