"""数据结构：兼容性、类型清洗、序列化回环。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sgs_zhanbao.models import (
    BiddingStrategy,
    BigMoment,
    HighlightRow,
    Match,
    ReportData,
    Spotlight,
)


class TestMatch:
    def test_header(self):
        m = Match(1, "开机大队", 14, 10, "败者食尘")
        assert m.header == "开机大队 14 : 10 败者食尘"

    def test_winner_a(self):
        assert Match(1, "A", 14, 10, "B").winner == "A"

    def test_winner_b(self):
        assert Match(1, "A", 4, 25, "B").winner == "B"

    def test_draw_has_no_winner(self):
        assert Match(1, "A", 10, 10, "B").winner == ""


class TestFromDict:
    def test_minimal(self):
        r = ReportData.from_dict({})
        assert r.title == ""
        assert r.matches == []
        assert isinstance(r.bidding, BiddingStrategy)

    def test_ignores_extra_keys(self):
        r = ReportData.from_dict({"title": "t", "不认识的字段": 1})
        assert r.title == "t"

    def test_score_string_coerced_to_int(self):
        r = ReportData.from_dict(
            {"matches": [{"index": "2", "team_a": "A", "score_a": "14", "score_b": 10, "team_b": "B"}]}
        )
        assert r.matches[0].index == 2
        assert r.matches[0].score_a == 14
        assert isinstance(r.matches[0].score_a, int)

    def test_bad_score_falls_back_to_zero(self):
        r = ReportData.from_dict({"matches": [{"score_a": "abc"}]})
        assert r.matches[0].score_a == 0

    def test_none_string_becomes_empty(self):
        r = ReportData.from_dict({"title": None, "spotlights": [{"title": None, "body": "x"}]})
        assert r.title == ""
        assert r.spotlights[0].title == ""

    def test_list_value_joined(self):
        r = ReportData.from_dict({"title": ["赛事", "速递"]})
        assert r.title == "赛事 速递"

    def test_string_in_list_becomes_body(self):
        r = ReportData.from_dict({"moments": ["只有正文的一条"]})
        assert r.moments[0].body == "只有正文的一条"

    def test_non_list_field_ignored(self):
        r = ReportData.from_dict({"spotlights": "不是列表"})
        assert r.spotlights == []

    def test_bidding_legacy_key_round_only(self):
        r = ReportData.from_dict({"bidding": {"key_round": "刘焉反叫4分"}})
        assert r.bidding.entries() == ["刘焉反叫4分"]

    def test_bidding_as_string(self):
        r = ReportData.from_dict({"bidding": "叫分很激进"})
        assert r.bidding.team_a == "叫分很激进"

    def test_meta_non_dict_replaced(self):
        r = ReportData.from_dict({"meta": "应该是对象"})
        assert r.meta == {}

    def test_requires_dict(self):
        with pytest.raises(TypeError):
            ReportData.from_dict([1, 2, 3])


class TestSerialisation:
    def test_roundtrip(self, report_dict: dict):
        r = ReportData.from_dict(report_dict)
        again = ReportData.from_dict(r.to_dict())
        assert again.to_dict() == r.to_dict()

    def test_save_and_load(self, tmp_path: Path, report_dict: dict):
        r = ReportData.from_dict(report_dict)
        p = r.save(tmp_path / "sub" / "report.json")
        assert p.is_file()
        loaded = ReportData.load(p)
        assert loaded.title == r.title
        assert loaded.matches[0].team_a == "开机大队"

    def test_saved_json_is_utf8_readable(self, tmp_path: Path, report_dict: dict):
        r = ReportData.from_dict(report_dict)
        p = r.save(tmp_path / "r.json")
        raw = p.read_text(encoding="utf-8")
        assert "开机大队" in raw, "中文不该被转义，人工编辑时才好看"
        assert json.loads(raw)["title"] == r.title


class TestHelpers:
    def test_reporters(self):
        r = ReportData.from_dict(
            {
                "matches": [
                    {"team_a": "A", "score_a": 5, "score_b": 1, "team_b": "B"},
                    {"team_a": "C", "score_a": 1, "score_b": 5, "team_b": "D"},
                ]
            }
        )
        assert r.reporters == ["A", "D"]

    def test_is_empty(self):
        assert ReportData().is_empty()
        assert ReportData(title="只有标题").is_empty(), "标题不算内容"
        assert not ReportData(tomorrow="明晚7点").is_empty()
        assert not ReportData(highlights=[HighlightRow("最强地主", "魔孙权")]).is_empty()

    def test_bidding_entries_order(self):
        b = BiddingStrategy(team_a="A：1", team_c="C：3", key_round="K")
        assert b.entries() == ["A：1", "C：3", "K"]

    def test_bidding_entries_skips_blank(self):
        assert BiddingStrategy(team_a="  ", team_b="").entries() == []


class TestDataclassDefaults:
    @pytest.mark.parametrize(
        "cls", [Match, Spotlight, BigMoment, HighlightRow, BiddingStrategy]
    )
    def test_constructible_with_no_args(self, cls):
        """所有结构都要能空构造，渲染层才能放心假设字段存在。"""
        cls()
