"""校正稿 → 结构化战报数据。

前提：文字纠错已由独立步骤完成（:mod:`sgs_zhanbao.proofread`）。本模块不含任何
纠错提示词，只负责「读稿 → 提炼 → 结构化」。存在 ``<stem>.corrected.txt`` 时自动
优先读它；``<stem>.corrections.json`` 只用于给 ``meta`` 写统计，不进正文。

两段式提取
----------
1. 转写稿按时间块切分，逐块让模型做「局部摘要」——抓住细节，避免超长截断
2. 把全部块摘要 + 原文首尾喂给模型，产出最终 JSON

这样 1.5 小时的稿子也不会因为超长而丢掉后半段。
短稿（低于 ``summary_threshold`` 字）会跳过第一段，省一半 token。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from .config import Config
from .ingest import guess_date, load_transcript
from .llm import LLMClient, LLMError
from .models import ReportData
from .proofread import default_corrected_path, default_corrections_path, load_corrections

Log = Callable[[str], None]

CHUNK_SUMMARY_PROMPT = """你是三国杀赛事记录员。下面是解说录音的一个时间片段（{idx}/{total}）。

请提取这个片段里所有**事实性信息**，用要点列出，不要展开成文章：
- 对局进程：谁打谁、用了什么武将、关键牌（杀/闪/桃/装备）、血量变化、胜负
- 叫分：谁叫了几分、反叫、是否地主
- 比分变化
- 解说的评价、吐槽、玩梗（保留原话）
- 提到的赛程/队伍/后续安排

【片段内容】
{chunk}

只输出要点列表，不要客套话。"""

# 正文里给模型的原文上限（超了留头尾：开头是背景，结尾常有赛果和预告）
MAX_RAW_CHARS = 24000
# 低于这个字数就不做分块摘要了，直接一次提取
SUMMARY_THRESHOLD = 6000


def _read_prompt(cfg: Config, name: str) -> str:
    p = cfg.prompt_dir() / name
    if not p.is_file():
        raise FileNotFoundError(f"找不到提示词文件：{p}")
    return p.read_text(encoding="utf-8")


def _summarize_chunks(
    client: LLMClient, chunks: list[str], *, log: Log = print
) -> str:
    """逐块摘要，拼成全局上下文。"""
    parts: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        log(f"    摘要 {i}/{len(chunks)} …")
        summary = client.chat(
            [{"role": "user", "content": CHUNK_SUMMARY_PROMPT.format(
                idx=i, total=len(chunks), chunk=chunk)}],
            temperature=0.2,
        )
        parts.append(f"### 片段 {i}\n{summary.strip()}")
    return "\n\n".join(parts)


def _fix_title(title: str, date: str) -> str:
    """标题里的日期强制跟随实际日期，并统一成「赛事速递 | M.D 描述」。"""
    if not title or not date:
        return title
    parts = date.split("-")
    if len(parts) != 3:
        return title
    try:
        md = f"{int(parts[1])}.{int(parts[2])}"
    except ValueError:
        return title
    rest = title.split("|", 1)[1].strip() if "|" in title else title
    rest = re.sub(r"^\d{1,2}\.\d{1,2}\s*", "", rest)
    rest = rest.strip()
    if not rest:
        return f"赛事速递 | {md} 战报"
    return f"赛事速递 | {md} {rest}"


def resolve_corrected(
    transcript_path: str | Path, corrected_path: str | Path | None = None
) -> Path:
    """优先用校对稿；没有就退回原稿。"""
    original = Path(transcript_path)
    if corrected_path:
        p = Path(corrected_path)
        return p if p.is_file() else original
    auto = default_corrected_path(original)
    return auto if auto.is_file() else original


def extract(
    transcript_path: str | Path,
    score_info: str = "",
    *,
    client: LLMClient | None = None,
    cfg: Config | None = None,
    date: str = "",
    corrected_path: str | Path | None = None,
    summarize: bool | None = None,
    log: Log = print,
) -> ReportData:
    """跑一次提取，返回 :class:`ReportData`（已注入日期、标题、meta）。

    ``summarize=None`` 时按字数自动决定要不要分块摘要。
    """
    cfg = cfg or Config()
    client = client or LLMClient(cfg)
    transcript_path = Path(transcript_path)
    report_date = date or guess_date(transcript_path)

    source = resolve_corrected(transcript_path, corrected_path)
    if source == transcript_path:
        log("  ⚠ 未找到校正稿，直接使用原稿（建议先跑 proofread）")
    else:
        log(f"  读入校正稿：{source.name}")

    tr = load_transcript(source)
    log(f"  {tr['n_segments']} 段 / {tr['total_chars']} 字 / 切分 {len(tr['chunks'])} 块")

    need_summary = tr["total_chars"] > SUMMARY_THRESHOLD if summarize is None else summarize
    if need_summary:
        log("  逐块摘要（保留细节，防止超长丢失）…")
        digest = _summarize_chunks(client, tr["chunks"], log=log)
        transcript_block_head = f"## 分段摘要（按时间顺序）\n\n{digest}\n\n"
    else:
        log("  短稿，跳过分块摘要（直接提取）")
        transcript_block_head = ""

    raw_text = "\n".join(
        f"[{s.timestamp}] {s.text}" if s.timestamp else s.text for s in tr["segments"]
    )
    if len(raw_text) > MAX_RAW_CHARS:
        half = MAX_RAW_CHARS // 2
        raw_text = (
            raw_text[:half] + "\n\n……（中间省略）……\n\n" + raw_text[-half:]
        )

    transcript_block = (
        f"{transcript_block_head}"
        f"## 转写原文（用于核对细节与找原话）\n\n{raw_text}"
    )

    filled = _read_prompt(cfg, "extract_report.md")
    filled = filled.replace("{date}", report_date or "（未提供）")
    filled = filled.replace("{score_info}", score_info or "（未提供，请从录音推断）")
    filled = filled.replace("{transcript}", transcript_block)

    log("  生成结构化战报…")
    data = client.chat_json(
        [
            {
                "role": "system",
                "content": "你只输出合法 JSON，不输出任何解释文字。严格遵循用户给出的字段结构。",
            },
            {"role": "user", "content": filled},
        ],
        max_tokens=cfg.llm.max_tokens,
    )
    if not isinstance(data, dict):
        raise LLMError(f"模型返回的不是 JSON 对象：{type(data).__name__}")

    report = ReportData.from_dict(data)
    report.date = report_date
    report.title = _fix_title(report.title, report_date) or (
        f"赛事速递 | {report.date} 战报" if report.date else "赛事速递 | 战报"
    )
    report.meta.setdefault("source", source.name)

    _attach_proofread_meta(report, transcript_path, log=log)
    report.meta["usage"] = client.usage.as_dict()

    if report.is_empty():
        raise LLMError(
            "抽取结果为空（没有比赛、焦点局、名场面、看点中的任何一项）。\n"
            "多半是转写稿格式没被正确解析，或转写内容与赛事无关。"
        )
    return report


def _attach_proofread_meta(report: ReportData, transcript_path: Path, *, log: Log) -> None:
    """把校对统计塞进 meta（只有统计，没有具体修正条目）。"""
    corr_path = default_corrections_path(transcript_path)
    corr_meta, corrections = load_corrections(corr_path)
    if not corrections:
        return
    applied = sum(1 for c in corrections if c.applied)
    report.meta["proofread"] = {
        "source": corr_path.name,
        "chunks": corr_meta.get("n_chunks"),
        "failed_chunks": corr_meta.get("failed_chunks") or [],
        "applied": applied,
        "pending": len(corrections) - applied,
    }
    log(f"  校对统计：采信 {applied} 处 / 待确认 {len(corrections) - applied} 处（不进正文）")


def load_report(path: str | Path) -> ReportData:
    """读一份已有 report.json（手改过的也能读）。"""
    return ReportData.load(path)


def dump_report(report: ReportData, path: str | Path) -> Path:
    return report.save(path)


def report_to_json(report: ReportData) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
