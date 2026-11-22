"""战报数据结构。

用 dataclass 而不是裸 dict：让 LLM 的输出必须落进固定槽位，渲染层可以放心假设
字段存在；同时 ``from_dict`` 会丢掉模型多给的键、对类型做就近转换，
这样一份手改过的 JSON 也能直接渲染。

字段演进
--------
``bidding`` 早期只有 ``team_a/team_b + key_round``，现在支持 ``team_a..team_d``。
两个版本都能读、都能渲染，历史 ``report.json`` 不需要迁移。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


# ---------------------------------------------------------------- 小工具

def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        m = value.strip()
        if m.isdigit() or (m.startswith("-") and m[1:].isdigit()):
            return int(m)
    return default


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return " ".join(_as_str(v) for v in value if v is not None)
    return str(value).strip()


def _build(cls: type[T], raw: Any) -> T:
    """只取 dataclass 已声明的字段，并对类型做就近转换。"""
    if not isinstance(raw, dict):
        raise TypeError(f"{cls.__name__} 需要 dict，收到 {type(raw).__name__}")
    declared = {f.name: f for f in fields(cls)}  # type: ignore[arg-type]
    kwargs: dict[str, Any] = {}
    for name, f in declared.items():
        if name not in raw:
            continue
        value = raw[name]
        ann = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", "")
        if ann == "int":
            kwargs[name] = _as_int(value)
        elif ann == "str":
            kwargs[name] = _as_str(value)
        else:
            kwargs[name] = value
    return cls(**kwargs)  # type: ignore[call-arg]


# ---------------------------------------------------------------- 结构

@dataclass
class Match:
    """一场 BO 系列赛（一日赛事里通常有 1-2 场）。"""

    index: int = 1                  # 第几场，从 1 开始
    team_a: str = ""
    score_a: int = 0
    score_b: int = 0
    team_b: str = ""
    note: str = ""                  # 例如「败者食尘」

    @property
    def header(self) -> str:
        return f"{self.team_a} {self.score_a} : {self.score_b} {self.team_b}"

    @property
    def winner(self) -> str:
        if self.score_a > self.score_b:
            return self.team_a
        if self.score_b > self.score_a:
            return self.team_b
        return ""


@dataclass
class Spotlight:
    """焦点局：一场比赛里最精彩的 1-2 个回合。"""

    title: str = ""
    body: str = ""


@dataclass
class BigMoment:
    """名场面：最有梗 / 最戏剧性的瞬间。"""

    title: str = ""
    body: str = ""


@dataclass
class BiddingStrategy:
    """叫分策略。

    ``team_a..team_d``：一天 4 支队伍各一条，取值形如「队名：正文」；
    ``key_round``：旧版字段，保留以兼容历史数据。
    """

    team_a: str = ""
    team_b: str = ""
    team_c: str = ""
    team_d: str = ""
    key_round: str = ""
    commentary: str = ""

    def entries(self) -> list[str]:
        out = [
            v for v in (self.team_a, self.team_b, self.team_c, self.team_d)
            if v.strip()
        ]
        if self.key_round.strip():
            out.append(self.key_round)
        return out


@dataclass
class HighlightRow:
    """当日看点总结表的一行。"""

    label: str = ""
    detail: str = ""


@dataclass
class ReportData:
    """一份完整战报的结构化数据。

    注意：语音转文字的修正清单**不属于战报**，由 :mod:`sgs_zhanbao.proofread`
    单独产出到 ``<stem>.corrections.json``，不进入这里。
    """

    title: str = ""                             # 赛事速递 | 11.5 SGL主播赛战报
    date: str = ""                              # 2026-11-05（由调用方注入，不让模型生成）
    matches: list[Match] = field(default_factory=list)
    spotlights: list[Spotlight] = field(default_factory=list)
    moments: list[BigMoment] = field(default_factory=list)
    bidding: BiddingStrategy = field(default_factory=BiddingStrategy)
    highlights: list[HighlightRow] = field(default_factory=list)
    tomorrow: str = ""                          # 明日预告
    full_text: str = ""                         # 原样保留的完整正文（Markdown），供对照
    meta: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------- 便捷属性

    @property
    def reporters(self) -> list[str]:
        """胜者队名列表（渲染底部祝贺语用）。"""
        return [m.winner for m in self.matches if m.winner]

    def is_empty(self) -> bool:
        return not any(
            [self.matches, self.spotlights, self.moments, self.highlights,
             self.bidding.entries(), self.tomorrow]
        )

    # ---------------------------------------------------------- 序列化

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return p

    @classmethod
    def load(cls, path: str | Path) -> ReportData:
        p = Path(path)
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, raw: Any) -> ReportData:
        if not isinstance(raw, dict):
            raise TypeError(f"ReportData 需要 dict，收到 {type(raw).__name__}")
        data = dict(raw)
        data["matches"] = [_build(Match, x) for x in _dicts(raw.get("matches"))]
        data["spotlights"] = [_build(Spotlight, x) for x in _dicts(raw.get("spotlights"))]
        data["moments"] = [_build(BigMoment, x) for x in _dicts(raw.get("moments"))]
        data["highlights"] = [_build(HighlightRow, x) for x in _dicts(raw.get("highlights"))]
        bidding = raw.get("bidding")
        if isinstance(bidding, dict):
            data["bidding"] = _build(BiddingStrategy, bidding)
        elif isinstance(bidding, str):
            # 老数据偶尔把叫分写成一大段文字
            data["bidding"] = BiddingStrategy(team_a=bidding)
        else:
            data["bidding"] = BiddingStrategy()
        if not isinstance(data.get("meta"), dict):
            data["meta"] = {}
        return _build(cls, data)


def _dicts(value: Any) -> list[dict]:
    """把可能是 None / 非列表 / 混入字符串的字段洗成 dict 列表。"""
    if not isinstance(value, (list, tuple)):
        return []
    out: list[dict] = []
    for item in value:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str) and item.strip():
            # 模型偶尔给一条纯字符串，当成「无标题条目」
            out.append({"body": item.strip()})
    return out
