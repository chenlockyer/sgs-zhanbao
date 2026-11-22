"""转写稿读取、清洗、切块。

支持三种输入格式（自动识别，无需指定）：

1. **腾讯会议逐字稿**（本项目主力）::

       解说甲 共享音频(00:19:09): 好像又有点显示的 bug 在手上……

2. **带方括号时间戳的纯文本**（本地 ASR / 流式转写产物）::

       [00:19:09] 好像又有点显示的 bug 在手上……

3. **没有任何时间戳的纯文本**：按行读，时间戳留空。

清洗原则：**只做安全处理，不删信息**。像「曹感三」这类玩梗，正则删掉就再也找不回来了，
必须交给 LLM 结合上下文判断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- 行格式

# 腾讯会议：<发言人> [共享音频]<(HH:MM:SS)>: <内容>
LINE_RE = re.compile(
    r"^(?P<speaker>[^(:：\[\]]{1,30}?)\s*"
    r"(?P<shared>共享音频|共享屏幕|共享视频)?\s*"
    r"[\(（](?P<ts>\d{1,2}:\d{2}(?::\d{2})?)[\)）]\s*[:：]?\s*"
    r"(?P<text>.+)$"
)

# 方括号时间戳：[00:19:09] 内容   /   [00:19] 内容
BRACKET_TS_RE = re.compile(
    r"^\[(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?P<text>.+)$"
)

# 纯口头填充 / 转写噪声（只在 clean_text 里用，且只用能确定无损的）
# 只删填充词本身，**保留后面的标点**——否则「牌啊，很强」会变成「牌很强」，读起来更差
FILLER_RE = re.compile(r"(?:^|[，,。；;])\s*(?:嗯+|啊+|呃+|哦+)\s*|(?:嗯+|啊+|呃+|哦+)(?=[，,])")
DUP_PUNCT_RE = re.compile(r"([，,。！？、])\1+")
SPACE_RE = re.compile(r"[ \t\u3000]+")

DEFAULT_SPEAKER = "解说"


@dataclass
class Segment:
    """一行转写（一句话）。"""

    timestamp: str          # 00:19:09；纯文本输入时为空
    speaker: str
    text: str
    prefix: str = ""        # 行首原样前缀（发言人 + 共享音频 + 时间戳 + 冒号）

    @property
    def seconds(self) -> int:
        parts = [int(x) for x in self.timestamp.split(":") if x.isdigit()]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return 0

    def render(self, *, keep_prefix: bool = True) -> str:
        if keep_prefix and self.prefix:
            return self.prefix + self.text
        stamp = f"[{self.timestamp}] " if self.timestamp else ""
        return f"{stamp}{self.text}"


# ---------------------------------------------------------------- 解析

def detect_format(text: str) -> str:
    """粗判输入格式，仅用于给用户提示和选择写回格式。"""
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if LINE_RE.match(s):
            return "tencent"
        if BRACKET_TS_RE.match(s):
            return "bracket"
    return "plain"


def parse_transcript(path: str | Path) -> list[Segment]:
    """按行解析，跳过空行。

    续行处理只对**带结构的格式**生效（腾讯会议偶尔把一句话断行）。
    纯文本输入则一行一句——那样才符合「一行一句话」的直觉。
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    segments: list[Segment] = []
    structured = False     # 是否见过带时间戳的行

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        m = LINE_RE.match(line)
        if m:
            structured = True
            segments.append(
                Segment(
                    timestamp=_norm_ts(m.group("ts")),
                    speaker=m.group("speaker").strip(),
                    text=m.group("text").strip(),
                    prefix=line[: m.start("text")],
                )
            )
            continue

        m = BRACKET_TS_RE.match(line)
        if m:
            structured = True
            segments.append(
                Segment(
                    timestamp=_norm_ts(m.group("ts")),
                    speaker=DEFAULT_SPEAKER,
                    text=m.group("text").strip(),
                    prefix=line[: m.start("text")],
                )
            )
            continue

        if structured and segments:
            # 续行：拼到上一段
            segments[-1].text += line
        else:
            segments.append(Segment(timestamp="", speaker=DEFAULT_SPEAKER, text=line))

    return segments


def _norm_ts(ts: str) -> str:
    """统一成 HH:MM:SS（输入可能是 M:SS）。"""
    parts = [int(x) for x in ts.split(":")]
    if len(parts) == 2:
        return f"{parts[0] // 60:02d}:{parts[0] % 60:02d}:{parts[1]:02d}"
    if len(parts) == 3:
        return f"{parts[0]:02d}:{parts[1]:02d}:{parts[2]:02d}"
    return ts


def write_transcript(
    segments: list[Segment],
    path: str | Path,
    *,
    style: str = "auto",
) -> Path:
    """把 segment 写回文件（用于输出校正稿）。

    ``style``：

    * ``tencent``：腾讯会议同款格式，逐行保留原前缀
    * ``bracket``：``[HH:MM:SS] 内容``
    * ``auto``：跟着输入走（有原前缀就沿用，否则用方括号时间戳）
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for s in segments:
        if style == "tencent" or (style == "auto" and s.prefix):
            prefix = s.prefix or f"{s.speaker} 共享音频({s.timestamp}): "
            lines.append(prefix + s.text)
        else:
            lines.append(s.render(keep_prefix=False))

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def guess_date(path: str | Path) -> str:
    """从文件名 20261105... 抠日期；抠不到返回空串。"""
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", Path(path).name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


# ---------------------------------------------------------------- 清洗

def clean_text(text: str) -> str:
    """轻量清洗：只做**明显无损**的处理。

    * 折叠空白
    * 删掉「嗯」「啊」这类纯填充词（保留其后的标点）
    * 合并重复标点

    不改词、不换字、不删感叹——那些要交给校对步骤（见 proofread.py）。
    实测：``"嗯，这个牌啊，很强"`` → ``"这个牌，很强"``，读起来通顺且没丢信息。
    """
    s = SPACE_RE.sub(" ", text)
    s = FILLER_RE.sub("", s)
    s = DUP_PUNCT_RE.sub(r"\1", s)
    return s.strip(" ，,")


def merge_segments(segments: list[Segment], max_chars: int = 1800) -> list[str]:
    """把零碎句子合并成语义块，每块控制在 ``max_chars`` 左右。

    块太碎模型抓不住上下文，块太大又会丢细节。1800 字约等于 3-5 分钟解说。
    单条句子本身超长时独占一块（不做截断，免得丢内容）。
    """
    chunks: list[str] = []
    buf: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal buf, size
        if buf:
            chunks.append("\n".join(buf))
            buf, size = [], 0

    for seg in segments:
        t = clean_text(seg.text)
        if not t:
            continue
        piece = f"[{seg.timestamp}] {t}" if seg.timestamp else t
        if buf and size + len(piece) > max_chars:
            flush()
        buf.append(piece)
        size += len(piece)

    flush()
    return chunks


def load_transcript(path: str | Path) -> dict:
    """读入 + 统计，返回后续步骤需要的结构。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"找不到转写稿：{p}")

    segs = parse_transcript(p)
    if not segs:
        raise ValueError(
            f"没有解析出任何内容，检查文件格式或编码：{p}\n"
            f"支持的格式见 docs/输入格式.md"
        )

    raw = p.read_text(encoding="utf-8-sig", errors="replace")
    fmt = detect_format(raw)

    return {
        "path": str(p),
        "format": fmt,
        "segments": segs,
        "chunks": merge_segments(segs),
        "n_segments": len(segs),
        "total_chars": sum(len(s.text) for s in segs),
        "duration": segs[-1].timestamp if segs else "",
        "speakers": sorted({s.speaker for s in segs}),
    }
