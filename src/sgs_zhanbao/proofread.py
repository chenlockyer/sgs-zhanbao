"""转写稿校对（与战报生成彻底解耦的独立步骤）。

为什么单独一步
--------------
战报生成模型如果同时做「纠错 + 提炼 + 写稿」，纠错指令会污染写稿判断：
没把握的字硬改、玩梗被「规范化」、术语对照表挤占正文注意力。
所以这里把纠错拆成**独立上下文**，它只干一件事：找出语音转文字的同音/近音错误，
产出「修正清单 + 校正后逐字稿」。下游的 extract 只读校正后的稿子。

产物（都落在原稿旁边，方便人工复核）：

* ``<stem>.corrected.txt``     校正后的逐字稿（行格式与原稿一致）
* ``<stem>.corrections.json``  修正清单（含 confidence=low 的待确认项）

四道硬校验（模型骗不过去）：

1. ``wrong`` 必须**逐字出现在本片段原文里**，否则丢弃（防幻觉、防概括）
2. ``wrong == right`` / 超长 / 空值 直接丢弃
3. 终审（第二个独立上下文）否决的修正降级为「待确认」，不写进逐字稿
4. 替换时统计实际命中次数，``applied == 0`` 的标「未应用」；
   且替换会跳过「已经是正确写法」的位置（不会把「无中生有」改成「无无中生有」）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from .config import RES, Config
from .ingest import Segment, load_transcript, write_transcript
from .llm import LLMClient, LLMError

# 只应用高置信修正；低置信只记录
APPLY_CONFIDENCE = {"high"}
# 每批终审多少条候选
VERIFY_BATCH = 25
# 单条修正的长度上限（防止模型贴整段）
MAX_WRONG_LEN = 40
MAX_RIGHT_LEN = 60

Log = Callable[[str], None]


# ============================================================ 数据结构

@dataclass
class Correction:
    """一条修正记录。"""

    wrong: str
    right: str
    ts: str = ""              # 所在行时间戳，空表示全片段替换
    confidence: str = "high"  # high / medium / low
    category: str = ""        # 战队名 / 武将名 / ...
    note: str = ""
    applied: int = 0          # 实际替换次数（0 = 未采信 / 原文里没找到）

    @property
    def is_applied(self) -> bool:
        return self.applied > 0

    @property
    def is_pending(self) -> bool:
        return not self.is_applied

    def tag(self) -> str:
        if self.is_applied:
            return "采信"
        return "待确认" if self.confidence in {"medium", "low"} else "未应用"


@dataclass
class ProofreadResult:
    """一次校对的结果。"""

    source: Path
    corrected_path: Path
    corrections_path: Path
    corrections: list[Correction] = field(default_factory=list)
    n_chunks: int = 0
    failed_chunks: list[int] = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    @property
    def applied_count(self) -> int:
        return sum(1 for c in self.corrections if c.is_applied)

    @property
    def pending_count(self) -> int:
        return len(self.corrections) - self.applied_count

    def summary(self) -> str:
        extra = f"，失败片段 {self.failed_chunks}" if self.failed_chunks else ""
        return (
            f"采信 {self.applied_count} 处 / 待确认 {self.pending_count} 处"
            f"（{self.n_chunks} 块{extra}）"
        )


# ============================================================ 术语表

def load_glossary(path: str | Path | None = None) -> dict:
    p = Path(path) if path else RES.glossary
    if not p.is_file():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def render_glossary(g: dict) -> str:
    """把 glossary.yaml 压成紧凑的「正确写法 ← 常见误写」文本，喂给校对模型。"""
    if not g:
        return "（无术语表）"

    out: list[str] = []

    def _section(title: str) -> None:
        out.append(f"## {title}")

    def _map_section(title: str, mapping: Any) -> None:
        if not isinstance(mapping, dict) or not mapping:
            return
        _section(f"{title}（正确写法 ← 常见误写）")
        for right, wrongly in mapping.items():
            wrongs = wrongly if isinstance(wrongly, (list, tuple)) else [wrongly]
            out.append(f"- {right} ← {' / '.join(str(w) for w in wrongs)}")

    _map_section("战队/机构", g.get("teams"))
    _map_section("解说/选手", g.get("casters"))

    if generals := g.get("generals"):
        _section("武将（正确写法，出现近音字时按此还原）")
        out.append("、".join(str(x) for x in generals))
    if prefixes := g.get("series_prefixes"):
        out.append(f"前缀不能丢：{'、'.join(str(x) for x in prefixes)}")

    if skills := g.get("skills"):
        _section("技能（正确写法）")
        out.append("、".join(str(x) for x in skills))

    equipment = g.get("equipment") or {}
    if isinstance(equipment, dict):
        for group, items in equipment.items():
            if items:
                _section(f"装备·{group}")
                out.append("、".join(str(x) for x in items))

    if cards := g.get("cards"):
        _section("卡牌")
        out.append("、".join(str(x) for x in cards))

    if memes := g.get("memes"):
        _section("玩梗/黑话（必须保留原样）")
        for word, desc in memes.items():
            out.append(f"- {word}：{desc}")

    if notes := g.get("format_notes"):
        _section("赛制常识")
        for line in notes:
            out.append(f"- {line}")

    return "\n".join(out)


# ============================================================ 提示词

def _read_prompt(name: str, prompt_dir: Path) -> str:
    p = prompt_dir / name
    if not p.is_file():
        raise FileNotFoundError(f"找不到提示词文件：{p}")
    return p.read_text(encoding="utf-8")


def _fill(template: str, **kw: str) -> str:
    for k, v in kw.items():
        template = template.replace("{" + k + "}", v)
    return template


# ============================================================ 单片段校对

def _parse_corrections(payload: Any, chunk_text: str) -> list[Correction]:
    """校验模型输出，只留「wrong 确实出现在片段里」的条目。"""
    if isinstance(payload, dict):
        items = payload.get("corrections")
    elif isinstance(payload, list):
        items = payload
    else:
        items = None
    if not isinstance(items, list):
        return []

    found: list[Correction] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        wrong = str(raw.get("wrong", "")).strip()
        right = str(raw.get("right", "")).strip()
        if not wrong or not right or wrong == right:
            continue
        if len(wrong) > MAX_WRONG_LEN or len(right) > MAX_RIGHT_LEN:
            continue
        # 硬校验：模型必须引用原文，防止幻觉/概括
        if wrong not in chunk_text:
            continue
        confidence = str(raw.get("confidence", "high")).strip().lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        found.append(
            Correction(
                wrong=wrong,
                right=right,
                ts=str(raw.get("ts", "")).strip(),
                confidence=confidence,
                category=str(raw.get("category", "")).strip(),
                note=str(raw.get("note", "")).strip(),
            )
        )
    return found


def _proofread_chunk(
    client: LLMClient,
    chunk: str,
    idx: int,
    total: int,
    glossary_text: str,
    *,
    prompt_dir: Path,
    log: Log = print,
    attempts: int = 2,
) -> list[Correction]:
    prompt = _fill(
        _read_prompt("proofread.md", prompt_dir),
        glossary=glossary_text,
        idx=str(idx),
        total=str(total),
        chunk=chunk,
    )
    messages = [
        {"role": "system", "content": "你是专职转写校对员，只输出合法 JSON。不要输出任何解释文字。"},
        {"role": "user", "content": prompt},
    ]
    last_err: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            payload = client.chat_json(messages, temperature=0.0, max_tokens=4096)
            return _parse_corrections(payload, chunk)
        except (LLMError, ValueError, TypeError) as exc:
            last_err = exc
            if i < attempts:
                log(f"      片段 {idx} 第 {i} 次解析失败，重试：{exc}")
    raise LLMError(f"片段 {idx} 校对失败：{last_err}")


# ============================================================ 终审

def _sentence_for(c: Correction, segments: list[Segment], pad: int = 55) -> str:
    """取 wrong 所在句子（原文），给终审员看上下文。"""
    for s in segments:
        if c.wrong in s.text:
            t = s.text
            i = t.find(c.wrong)
            a, b = max(0, i - pad), min(len(t), i + len(c.wrong) + pad)
            return ("…" if a else "") + t[a:b] + ("…" if b < len(t) else "")
    return ""


def _verify_corrections(
    client: LLMClient,
    corrections: list[Correction],
    segments: list[Segment],
    *,
    prompt_dir: Path,
    log: Log = print,
) -> list[Correction]:
    """对高置信候选做一次独立终审。

    单条校对只看 1800 字片段，容易把「无血条」拆成「无血」去改。这里把候选连同
    原句上下文交给另一个上下文逐条复核，否决的降级为待确认，不写进逐字稿。
    宁可漏改，不可改错。
    """
    todo = [c for c in corrections if c.confidence in APPLY_CONFIDENCE]
    if not todo:
        return corrections

    template = _read_prompt("verify_corrections.md", prompt_dir)
    rejected = 0

    for start in range(0, len(todo), VERIFY_BATCH):
        group = todo[start : start + VERIFY_BATCH]
        candidates = [
            {
                "index": start + i,          # 全局下标（不是批内下标）
                "wrong": c.wrong,
                "right": c.right,
                "sentence": _sentence_for(c, segments),
                "category": c.category,
                "note": c.note,
            }
            for i, c in enumerate(group)
        ]
        prompt = template.replace(
            "{candidates}", json.dumps(candidates, ensure_ascii=False, indent=1)
        )
        log(f"    终审 {start + 1}-{start + len(group)}/{len(todo)} …")

        payload: Any = None
        for attempt in (1, 2):
            try:
                payload = client.chat_json(
                    [
                        {"role": "system", "content": "你是校对终审员，只输出合法 JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                    max_tokens=4096,
                )
                break
            except (LLMError, ValueError, TypeError) as exc:
                log(f"      终审判次 {attempt} 失败：{exc}")

        verdicts = payload.get("verdicts") if isinstance(payload, dict) else None
        if not isinstance(verdicts, list):
            log("      ! 终审结果不可解析，本批保持原判")
            continue

        for v in verdicts:
            if not isinstance(v, dict):
                continue
            try:
                idx = int(v.get("index"))
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(todo):
                continue
            if v.get("keep") is False:
                c = todo[idx]
                reason = str(v.get("reason", "")).strip()
                c.confidence = "low"
                suffix = "终审否决" + (f"：{reason}" if reason else "")
                c.note = f"{c.note}；{suffix}".strip("；") if c.note else suffix
                rejected += 1

    log(f"  终审：否决 {rejected} 条高置信修正（降级为待确认）")
    return corrections


# ============================================================ 合并 / 应用

def _replace_all(text: str, wrong: str, right: str) -> tuple[str, int]:
    """单条安全替换：跳过「已经是正确写法」的位置。

    例：``wrong="无中" / right="无中生有"`` 作用在「无中生有」上，
    不能变成「无无中生有」。多条的联合替换见 :func:`_apply_one_pass`。
    """
    out: list[str] = []
    i = 0
    n = 0
    while True:
        j = text.find(wrong, i)
        if j < 0:
            out.append(text[i:])
            break
        if text.startswith(right, j):
            # 这里已经是正确写法（wrong 是 right 的前缀），整段照抄过去
            out.append(text[i : j + len(right)])
            i = j + len(right)
            continue
        out.append(text[i:j])
        out.append(right)
        i = j + len(wrong)
        n += 1
    return "".join(out), n


def _apply_one_pass(
    text: str, mapping: dict[str, str]
) -> tuple[str, dict[str, int]]:
    """一次扫描完成全部替换，**长词优先**。

    为什么必须单次扫描
    ------------------
    逐条替换会互相打架：先把「丈八蛇矛」改成「丈八长矛」，再让「丈八」→「丈八蛇矛」
    去扫一遍，就会得到「丈八蛇矛长矛」。一次扫描时，从每个位置起只匹配最长的那个
    ``wrong``，替换后直接跳过替换结果，后面不会再被二次改写。

    返回 (新文本, {wrong: 命中次数})。
    """
    if not mapping:
        return text, {}

    keys = sorted(mapping, key=len, reverse=True)
    counts: dict[str, int] = {}
    out: list[str] = []
    i = 0
    n = len(text)

    while i < n:
        matched = False
        for wrong in keys:
            if not text.startswith(wrong, i):
                continue
            right = mapping[wrong]
            # 已经是正确写法（wrong 是 right 的前缀）→ 整段照抄，不算一次修正
            if text.startswith(right, i):
                out.append(right)
                i += len(right)
            else:
                out.append(right)
                i += len(wrong)
                counts[wrong] = counts.get(wrong, 0) + 1
            matched = True
            break
        if not matched:
            out.append(text[i])
            i += 1

    return "".join(out), counts


def _dedupe(corrections: list[Correction], log: Log = print) -> list[Correction]:
    """同一 (wrong, right) 只留一条；同一 wrong 指向不同 right 时保留高置信度者。"""
    by_pair: dict[tuple[str, str], Correction] = {}
    by_wrong: dict[str, Correction] = {}
    rank = {"high": 3, "medium": 2, "low": 1}

    for c in corrections:
        pair = (c.wrong, c.right)
        if pair in by_pair:
            continue
        prev = by_wrong.get(c.wrong)
        if prev is not None and prev.right != c.right:
            keep, drop = (
                (prev, c) if rank[prev.confidence] >= rank[c.confidence] else (c, prev)
            )
            log(
                f"      ! 修正冲突：「{c.wrong}」→「{prev.right}」/「{c.right}」，"
                f"采信「{keep.right}」"
            )
            by_wrong[c.wrong] = keep
            by_pair.pop((drop.wrong, drop.right), None)
            by_pair[(keep.wrong, keep.right)] = keep
            continue
        by_pair[pair] = c
        by_wrong.setdefault(c.wrong, c)

    return list(by_pair.values())


def apply_corrections(
    segments: list[Segment],
    corrections: list[Correction],
    # log 保留在签名里：便于调用方注入进度输出，也保持与其它内部函数一致
    *,
    log: Log = print,  # noqa: ARG001
) -> list[Correction]:
    """把修正写回 segment 文本，返回带 applied 计数的清单。

    只应用 ``confidence=high`` 的条目；同一段内多条修正一次性完成（长词优先），
    避免「短词先把长词打散」和「先改完再被另一条改坏」。
    ``ts`` 非空的条目只作用于该时间戳所在行。
    """
    usable = [c for c in corrections if c.confidence in APPLY_CONFIDENCE]
    if not usable:
        return corrections

    by_ts: dict[str, dict[str, str]] = {}
    global_map: dict[str, str] = {}
    for c in usable:
        if c.ts:
            by_ts.setdefault(c.ts, {})[c.wrong] = c.right
        else:
            global_map[c.wrong] = c.right

    applied: dict[str, int] = {}

    for seg in segments:
        # 行级修正优先于全局（同一 wrong 在特定行有专门写法时以行为准）
        mapping = dict(global_map)
        if seg.timestamp in by_ts:
            mapping.update(by_ts[seg.timestamp])
        if not mapping:
            continue
        new_text, counts = _apply_one_pass(seg.text, mapping)
        if counts:
            seg.text = new_text
            for wrong, n in counts.items():
                applied[wrong] = applied.get(wrong, 0) + n

    for c in corrections:
        c.applied = applied.get(c.wrong, 0) if c.confidence in APPLY_CONFIDENCE else 0

    return corrections


def _order_for_report(
    corrections: list[Correction], segments: list[Segment]
) -> list[Correction]:
    """按在逐字稿中首次出现的位置排序，让清单读起来跟原文顺序一致。"""
    pos: dict[str, int] = {}
    for i, seg in enumerate(segments):
        for c in corrections:
            if c.wrong in seg.text and c.wrong not in pos:
                pos[c.wrong] = i
    return sorted(corrections, key=lambda c: (pos.get(c.wrong, 10**9), c.wrong))


# ============================================================ 产物路径

def default_corrected_path(transcript_path: str | Path) -> Path:
    p = Path(transcript_path)
    return p.with_name(p.stem + ".corrected.txt")


def default_corrections_path(transcript_path: str | Path) -> Path:
    p = Path(transcript_path)
    return p.with_name(p.stem + ".corrections.json")


# ============================================================ 主流程

def _save_corrections(
    path: Path,
    *,
    source: Path,
    corrected: Path,
    n_chunks: int,
    failed: list[int],
    usage: dict,
    corrections: list[Correction],
) -> None:
    payload = {
        "source": source.name,
        "corrected": corrected.name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_chunks": n_chunks,
        "failed_chunks": failed,
        "usage": usage,
        "corrections": [c.__dict__ for c in corrections],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def proofread(
    transcript_path: str | Path,
    *,
    client: LLMClient | None = None,
    cfg: Config | None = None,
    glossary_path: str | Path | None = None,
    limit: int = 0,
    log: Log = print,
) -> ProofreadResult:
    """转写稿 → 校正稿 + 修正清单。

    ``limit > 0`` 时只校对前 N 个片段（调试 / 试跑用）。
    """
    cfg = cfg or Config()
    client = client or LLMClient(cfg)
    transcript_path = Path(transcript_path)
    prompt_dir = cfg.prompt_dir()

    log(f"  读入转写稿：{transcript_path.name}")
    tr = load_transcript(transcript_path)
    log(
        f"  {tr['n_segments']} 段 / {tr['total_chars']} 字 / "
        f"切分 {len(tr['chunks'])} 块 / 格式 {tr['format']}"
    )

    chunks = tr["chunks"][:limit] if limit else tr["chunks"]
    if limit:
        log(f"  （调试模式：只校对前 {len(chunks)} 块）")

    glossary_text = render_glossary(load_glossary(glossary_path or cfg.glossary()))
    log(f"  术语表：{len(glossary_text)} 字")

    raw_corrections: list[Correction] = []
    failed: list[int] = []
    log("  逐段找错（独立上下文，每段一次调用）…")
    for i, chunk in enumerate(chunks, 1):
        log(f"    校对 {i}/{len(chunks)} …")
        try:
            found = _proofread_chunk(
                client, chunk, i, len(chunks), glossary_text,
                prompt_dir=prompt_dir, log=log,
            )
        except LLMError as exc:
            failed.append(i)
            log(f"      ! 片段 {i} 放弃：{exc}")
            continue
        raw_corrections.extend(found)

    corrections = _dedupe(raw_corrections, log=log)

    segments = tr["segments"]
    log("  终审（第二个独立上下文，逐条复核上下文）…")
    corrections = _verify_corrections(
        client, corrections, segments, prompt_dir=prompt_dir, log=log
    )

    apply_corrections(segments, corrections, log=log)
    corrections = _order_for_report(corrections, segments)

    corrected_path = default_corrected_path(transcript_path)
    write_transcript(segments, corrected_path, style=tr["format"])

    corrections_path = default_corrections_path(transcript_path)
    usage = client.usage.as_dict()
    _save_corrections(
        corrections_path,
        source=transcript_path,
        corrected=corrected_path,
        n_chunks=len(chunks),
        failed=failed,
        usage=usage,
        corrections=corrections,
    )

    log(f"  校正稿：{corrected_path}")
    log(f"  修正清单：{corrections_path}")
    return ProofreadResult(
        source=transcript_path,
        corrected_path=corrected_path,
        corrections_path=corrections_path,
        corrections=corrections,
        n_chunks=len(chunks),
        failed_chunks=failed,
        usage=usage,
    )


def load_corrections(path: str | Path) -> tuple[dict, list[Correction]]:
    """读回校对产物；不存在时返回空。"""
    p = Path(path)
    if not p.is_file():
        return {}, []
    payload = json.loads(p.read_text(encoding="utf-8"))
    items = payload.get("corrections") or []
    corrections = [
        Correction(
            wrong=str(x.get("wrong", "")),
            right=str(x.get("right", "")),
            ts=str(x.get("ts", "")),
            confidence=str(x.get("confidence", "high")),
            category=str(x.get("category", "")),
            note=str(x.get("note", "")),
            applied=int(x.get("applied", 0) or 0),
        )
        for x in items
        if isinstance(x, dict) and x.get("wrong") and x.get("right")
    ]
    return payload, corrections
