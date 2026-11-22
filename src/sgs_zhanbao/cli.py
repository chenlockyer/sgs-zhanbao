"""命令行入口。

安装后直接可用：``sgs-zhanbao --help``；不安装也能跑：``python -m sgs_zhanbao``。

子命令
------
``run``        一条命令跑完整流程（校对 → 提取 → 出图）
``proofread``  只做文字校对
``extract``    只做结构化提取
``render``     只渲染（改完 JSON / 提示词后重出图，不花 token）
``doctor``     自检：依赖、字体、素材、密钥

典型用法
--------
::

    sgs-zhanbao run --transcript voice2text/xxx.txt --score "开机大队 14:10 败者食尘"
    sgs-zhanbao proofread --transcript voice2text/xxx.txt --limit 2   # 先试 2 块
    sgs-zhanbao extract --transcript voice2text/xxx.txt --score "..."
    sgs-zhanbao render --data data/2026-11-05/report.json
    sgs-zhanbao doctor
"""

from __future__ import annotations

import argparse
import sys
from contextlib import suppress
from pathlib import Path

from . import __version__
from .config import (
    Config,
    data_dir,
    ensure_dirs,
    load_dotenv,
)

# ---------------------------------------------------------------- 输出

def _utf8_stdout() -> None:
    """Windows 控制台默认 GBK，强制 UTF-8 避免打印中文/符号时报错。"""
    for stream in (sys.stdout, sys.stderr):
        with suppress(Exception):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def _bootstrap(args) -> Config:
    """统一处理：UTF-8、.env、工作目录、配置对象。"""
    _utf8_stdout()
    root = Path(args.root).expanduser().resolve() if args.root else None
    if root:
        import os

        os.environ["SGS_HOME"] = str(root)
    load_dotenv()
    return Config(
        glossary_path=Path(args.glossary) if getattr(args, "glossary", None) else None,
        prompt_path=Path(args.prompts) if getattr(args, "prompts", None) else None,
        art_path=Path(args.art) if getattr(args, "art", None) else None,
        font_path=Path(args.font) if getattr(args, "font", None) else None,
    )


def _print_corrections(result) -> None:
    if not result.corrections:
        print("   （没有需要修正的错字）")
        return
    for c in result.corrections:
        sign = "✓" if c.is_applied else "?"
        count = f" ×{c.applied}" if c.applied else ""
        tag = c.category or c.tag()
        note = f"  — {c.note}" if c.note else ""
        print(f"   {sign} {c.wrong} → {c.right}  [{tag}]{count}{note}")


def _print_summary(r) -> None:
    print("\n—— 提取结果概览 ——")
    print(f"日期：{r.date}")
    print(f"标题：{r.title}")
    for m in r.matches:
        print(f"  第{m.index}场：{m.header}" + (f"（{m.note}）" if m.note else ""))
    print(
        f"焦点局 {len(r.spotlights)} 条 / 名场面 {len(r.moments)} 条 / "
        f"叫分 {len(r.bidding.entries())} 条 / 看点 {len(r.highlights)} 行"
    )
    if pf := r.meta.get("proofread"):
        print(
            f"校对统计（不进正文）：采信 {pf.get('applied')} 处 / "
            f"待确认 {pf.get('pending')} 处"
        )
    if notes := str(r.meta.get("notes") or "").strip():
        print(f"存疑点：{notes}")
    if usage := r.meta.get("usage"):
        print(f"本步用量：{usage}")


# ---------------------------------------------------------------- 子命令

def cmd_proofread(args) -> int:
    from .proofread import proofread

    cfg = _bootstrap(args)
    ensure_dirs()
    result = proofread(
        Path(args.transcript), cfg=cfg, limit=args.limit,
        glossary_path=cfg.glossary(),
    )
    print(f"\n✅ 校对完成：{result.summary()}")
    print(f"   校正稿：{result.corrected_path}")
    print(f"   修正清单：{result.corrections_path}")
    _print_corrections(result)
    return 0


def cmd_extract(args) -> int:
    from .extract import extract

    cfg = _bootstrap(args)
    ensure_dirs()
    report = extract(
        Path(args.transcript),
        score_info=args.score or "",
        cfg=cfg,
        date=args.date or "",
        corrected_path=Path(args.corrected) if args.corrected else None,
    )
    out = Path(args.out) if args.out else data_dir() / (report.date or "unknown") / "report.json"
    report.save(out)
    print(f"\n✅ 已保存：{out}")
    _print_summary(report)
    return 0


def cmd_render(args) -> int:
    from .render import render_report

    cfg = _bootstrap(args)
    out = render_report(
        Path(args.data), output_path=Path(args.out) if args.out else None, cfg=cfg,
    )
    print(f"✅ 已出图：{out}")
    return 0


def cmd_run(args) -> int:
    from .extract import extract
    from .proofread import default_corrected_path, default_corrections_path, proofread
    from .render import render_report

    cfg = _bootstrap(args)
    transcript = Path(args.transcript)
    if not transcript.is_file():
        print(f"✗ 找不到转写稿：{transcript}")
        return 2
    ensure_dirs()

    # ---------- 步骤 1/3：文字校对（独立上下文）----------
    corrected = default_corrected_path(transcript)
    corr_path = default_corrections_path(transcript)
    if args.no_proofread:
        print("▶ 步骤 1/3  文字校对（--no-proofread，跳过）")
    elif args.reuse and corrected.is_file() and corr_path.is_file():
        print("▶ 步骤 1/3  文字校对（--reuse，复用已有校正稿）")
    else:
        print("▶ 步骤 1/3  文字校对（独立上下文，只修语音转文字错字）")
        result = proofread(transcript, cfg=cfg, limit=args.limit)
        print(f"   ✓ {result.summary()}")
        _print_corrections(result)

    # ---------- 步骤 2/3：提取结构化数据 ----------
    print("\n▶ 步骤 2/3  提取结构化数据（只读校正稿，不做纠错）")
    report = extract(
        transcript, score_info=args.score or "", cfg=cfg, date=args.date or ""
    )
    data_path = data_dir() / (report.date or "unknown") / "report.json"
    report.save(data_path)
    _print_summary(report)

    # ---------- 步骤 3/3：渲染长图 ----------
    print("\n▶ 步骤 3/3  渲染长图")
    out = render_report(
        report, output_path=Path(args.out) if args.out else None, cfg=cfg
    )
    print(f"\n✅ 全部完成：{out}")
    print(f"   结构化数据：{data_path}")
    print("   提示：改完 report.json 后跑 `sgs-zhanbao render --data <json>` 可免 API 重出图")
    return 0


def cmd_doctor(args) -> int:
    """自检：把「为什么跑不起来」的原因一次性说清楚。"""
    import importlib
    import os

    from .config import RES, find_system_kaiti, home, load_api_key

    _utf8_stdout()
    load_dotenv()
    print(f"sgs-zhanbao {__version__}")
    print(f"Python {sys.version.split()[0]}  ({sys.executable})")
    print(f"工作目录：{home()}")
    ok = True

    # 依赖
    print("\n[依赖]")
    optional_render = True
    for mod, pkg, required in (
        ("yaml", "PyYAML", True),
        ("playwright", "playwright", optional_render),
        ("fontTools", "fonttools", optional_render),
    ):
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "")
            print(f"  ✓ {pkg} {ver}".rstrip())
        except ImportError:
            print(f"  ✗ {pkg} 未安装（pip install 'sgs-zhanbao[render]'）")
            if required:
                ok = False
            else:
                optional_render = False

    if optional_render:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                path = p.chromium.executable_path
                if not Path(path).is_file():
                    print(f"  ✗ chromium {path}")
                    print("      执行：python -m playwright install chromium")
                    ok = False
                else:
                    # 光有路径不够：playwright 实际可能启动 headless shell，
                    # 那是另一个二进制。只有真开一次浏览器才算装好。
                    browser = p.chromium.launch(args=["--no-sandbox"])
                    browser.close()
                    print(f"  ✓ chromium 可启动 {path}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ 无法启动 chromium：{str(exc).splitlines()[0]}")
            print("      执行：python -m playwright install chromium")
            ok = False

    # 素材
    print("\n[版式素材]")
    art = Config(art_path=Path(args.art) if args.art else None).art_dir()
    for name in ("head.png", "mid.png", "tail.png"):
        p = art / name
        mark = "✓" if p.is_file() else "✗"
        size = f"{p.stat().st_size // 1024} KB" if p.is_file() else "缺失"
        print(f"  {mark} {p}  {size}")
        if not p.is_file():
            ok = False

    # 提示词 / 术语表
    print("\n[提示词 / 术语表]")
    for p in (RES.prompts / "proofread.md", RES.prompts / "verify_corrections.md",
              RES.prompts / "extract_report.md", RES.glossary):
        mark = "✓" if p.is_file() else "✗"
        print(f"  {mark} {p.name}")
        if not p.is_file():
            ok = False

    # 字体
    print("\n[字体]")
    font = Path(args.font) if args.font else find_system_kaiti()
    if font and font.is_file():
        print(f"  ✓ {font}  {font.stat().st_size // 1024} KB")
    else:
        print("  ! 未找到楷体，将退回浏览器默认字体（观感会变差）")
        print("    把任意楷体 ttf 放到任意位置，然后设置 SGS_FONT=<路径>")

    # 密钥
    print("\n[密钥]")
    try:
        key = load_api_key(Config().llm.api_key_env)
        print(f"  ✓ {Config().llm.api_key_env} = {key[:6]}…{key[-4:]}（长度 {len(key)}）")
    except RuntimeError as exc:
        print(f"  ✗ {exc}")
        ok = False

    print("\n[环境]")
    print(f"  SGS_HOME={os.environ.get('SGS_HOME', '(未设置，用当前目录)')}")
    print(f"  base_url={Config().llm.base_url}  model={Config().llm.model}")
    print("\n" + ("✅ 自检通过" if ok else "⚠ 有项目未通过，按上面提示修"))
    return 0 if ok else 1


# ---------------------------------------------------------------- 参数

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sgs-zhanbao",
        description="三国杀赛事战报生成工作流：转写稿 → 校对 → 结构化 → 长图",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例：\n"
            "  sgs-zhanbao run --transcript voice2text/xxx.txt --score \"开机大队 14:10 败者食尘\"\n"
            "  sgs-zhanbao render --data data/2026-11-05/report.json\n"
            "  sgs-zhanbao doctor\n"
        ),
    )
    p.add_argument("--version", action="version", version=f"sgs-zhanbao {__version__}")

    # 公共参数放进 parent，每个子命令都能用（写在子命令之后）
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--root", default="", help="工作目录（默认当前目录，等价环境变量 SGS_HOME）"
    )
    common.add_argument("--glossary", default="", help="自定义术语表 yaml（默认用包内的）")
    common.add_argument("--prompts", default="", help="自定义提示词目录（默认用包内的）")
    common.add_argument("--art", default="", help="自定义版式素材目录（默认用包内的）")
    common.add_argument("--font", default="", help="自定义正文字体 ttf（默认用系统楷体）")

    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("proofread", parents=[common], help="转写稿 → 校正稿 + 修正清单")
    pp.add_argument("--transcript", required=True)
    pp.add_argument("--limit", type=int, default=0, help="只校对前 N 块（调试用）")
    pp.set_defaults(func=cmd_proofread)

    pe = sub.add_parser("extract", parents=[common], help="校正稿 → 结构化 report.json")
    pe.add_argument("--transcript", required=True, help="原始转写稿路径")
    pe.add_argument("--corrected", default="", help="显式指定校正稿（默认自动找 <stem>.corrected.txt）")
    pe.add_argument("--score", default="", help="赛果，如「第一场：开机大队 14:10 败者食尘」")
    pe.add_argument("--date", default="", help="比赛日期 YYYY-MM-DD（默认从文件名推断）")
    pe.add_argument("--out", default="", help="输出 JSON 路径")
    pe.set_defaults(func=cmd_extract)

    pr = sub.add_parser("render", parents=[common], help="report.json → PNG 长图")
    pr.add_argument("--data", required=True)
    pr.add_argument("--out", default="", help="输出 PNG 路径")
    pr.set_defaults(func=cmd_render)

    prun = sub.add_parser("run", parents=[common], help="一条命令跑完整流程")
    prun.add_argument("--transcript", required=True)
    prun.add_argument("--score", default="")
    prun.add_argument("--date", default="")
    prun.add_argument("--out", default="")
    prun.add_argument("--limit", type=int, default=0, help="校对只跑前 N 块（试跑用）")
    prun.add_argument("--reuse", action="store_true", help="已有校正稿时跳过校对")
    prun.add_argument("--no-proofread", action="store_true", help="不校对直接提取（不推荐）")
    prun.set_defaults(func=cmd_run)

    pd = sub.add_parser("doctor", parents=[common], help="自检依赖 / 素材 / 字体 / 密钥")
    pd.set_defaults(func=cmd_doctor)

    return p


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
