"""本地全自动链路的 CLI：录音 / 转写 / 一条命令跑完。

不依赖腾讯会议的路线::

    record      → WAV
    transcribe  → txt（带时间戳，格式与 ingest 兼容）
    run         → 录音 + 转写 + 战报提取 + 出图
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import data_dir, ensure_dirs, transcript_dir


def cmd_devices(a) -> int:  # noqa: ARG001 —— argparse 统一签名
    from .record import list_devices

    devs = list_devices()
    if not devs:
        print("没有找到回环设备（需要 Windows + PyAudioWPatch）")
        return 1
    for d in devs:
        print(f"[{d['index']}] {d['name']}  {d['rate']}Hz {d['channels']}ch")
    return 0


def cmd_record(a) -> int:
    from .record import record

    ensure_dirs()
    out = Path(a.out) if a.out else data_dir() / f"audio_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    record(out, duration=a.duration, device=a.device)
    return 0


def cmd_transcribe(a) -> int:
    from .asr import SenseVoice, transcribe_long

    ensure_dirs()
    wav = Path(a.wav)
    if not wav.is_file():
        print(f"✗ 找不到 {wav}")
        return 2

    rec = SenseVoice(num_threads=a.threads)
    result = transcribe_long(rec, wav, chunk_s=a.chunk)

    out = Path(a.out) if a.out else transcript_dir() / f"{time.strftime('%Y%m%d')}-本地转写-{wav.stem}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result["text"], encoding="utf-8")

    print(f"\n✅ 转写完成：{out}")
    print(
        f"   音频 {result['audio_seconds'] / 60:.1f} 分钟  "
        f"耗时 {result['elapsed_seconds'] / 60:.1f} 分钟  RTF {result['rtf']:.3f}"
    )
    print(f"\n继续跑战报：\n   sgs-zhanbao run --transcript {out}")
    return 0


def cmd_stream(a) -> int:
    """流式实时转写：边录边出稿，直播结束稿子已在手里。"""
    from .stream_asr import StreamingTranscriber, merge_lines_to_paragraphs

    ensure_dirs()
    out = Path(a.out) if a.out else transcript_dir() / f"{time.strftime('%Y%m%d_%H%M%S')}-流式转写.txt"

    tr = StreamingTranscriber(out, device_index=a.device, num_threads=a.threads)
    tr.start()
    print(f"流式转写中 → {out}\nCtrl+C 停止\n")

    t0 = time.time()
    try:
        while tr.running:
            time.sleep(2)
            wall = time.time() - t0
            lines = len(tr.lines)
            last = tr.lines[-1][:46] if tr.lines else ""
            print(
                f"\r  音频 {tr.audio_seconds / 60:5.1f}分  实际 {wall / 60:5.1f}分  "
                f"已出 {lines:4d} 句  {last}",
                end="", flush=True,
            )
    except KeyboardInterrupt:
        print("\n停止中…")
    finally:
        p = tr.stop()

    lines = p.read_text(encoding="utf-8").splitlines()
    merged = merge_lines_to_paragraphs(lines)
    merged_path = p.with_name(p.stem + "-段落版.txt")
    merged_path.write_text(merged, encoding="utf-8")

    print(f"\n✅ 流式转写完成：{p}")
    print(f"   共 {len([ln for ln in lines if ln.strip()])} 句，音频 {tr.audio_seconds / 60:.1f} 分钟")
    print(f"   段落版：{merged_path}")
    print(f"\n继续跑战报：\n   sgs-zhanbao run --transcript {merged_path}")
    return 0


def cmd_auto(a) -> int:
    """录 → 转 → 战报 → 出图，一条命令到底（需要人在场按 Ctrl+C 收麦）。"""
    from .asr import SenseVoice, transcribe_long
    from .extract import extract
    from .record import record
    from .render import render_report

    ensure_dirs()
    t_start = time.time()

    wav = data_dir() / f"audio_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    print(f"▶ 录音（Ctrl+C 结束录制并自动出图）→ {wav}")
    wav = record(wav, duration=a.duration)

    print("\n▶ 转写中（SenseVoice）…")
    sv = SenseVoice(num_threads=a.threads)
    result = transcribe_long(sv, wav, chunk_s=a.chunk)
    txt = transcript_dir() / f"{time.strftime('%Y%m%d')}-本地转写-{wav.stem}.txt"
    txt.write_text(result["text"], encoding="utf-8")
    print(f"✅ 转写完成 {txt.name}  RTF {result['rtf']:.3f}")

    print("\n▶ 提取战报…")
    report = extract(txt, score_info=a.score or "", date=a.date or "")
    out_json = data_dir() / (report.date or "unknown") / "report.json"
    report.save(out_json)

    print("\n▶ 渲染长图…")
    png = render_report(report)

    print(f"\n🎉 全部完成，总耗时 {(time.time() - t_start) / 60:.1f} 分钟")
    print(f"   图片  {png}")
    print(f"   数据  {out_json}")
    print(f"   转写  {txt}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="sgs-zhanbao-local", description="本地录音 + 离线转写（不依赖腾讯会议）"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices", help="列出回环设备").set_defaults(func=cmd_devices)

    pr = sub.add_parser("record", help="录音到 WAV")
    pr.add_argument("--out", default="")
    pr.add_argument("--device", type=int, default=None)
    pr.add_argument("--duration", type=float, default=None, help="录多少秒后自动停")
    pr.set_defaults(func=cmd_record)

    pt = sub.add_parser("transcribe", help="WAV → 文本")
    pt.add_argument("--wav", required=True)
    pt.add_argument("--out", default="")
    pt.add_argument("--chunk", type=float, default=30.0)
    pt.add_argument("--threads", type=int, default=4)
    pt.set_defaults(func=cmd_transcribe)

    ps = sub.add_parser("stream", help="流式实时转写（边录边转）")
    ps.add_argument("--out", default="")
    ps.add_argument("--device", type=int, default=None)
    ps.add_argument("--threads", type=int, default=4)
    ps.set_defaults(func=cmd_stream)

    pa = sub.add_parser("auto", help="录音→转写→战报→出图")
    pa.add_argument("--duration", type=float, default=None)
    pa.add_argument("--chunk", type=float, default=30.0)
    pa.add_argument("--threads", type=int, default=4)
    pa.add_argument("--score", default="")
    pa.add_argument("--date", default="")
    pa.set_defaults(func=cmd_auto)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已中断")
        return 130
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
