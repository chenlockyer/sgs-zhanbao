"""提取步骤（打桩 LLM）：标题修正、meta 注入、短稿跳过摘要、空结果报错。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sgs_zhanbao.extract import (
    _fix_title,
    extract,
    resolve_corrected,
)
from sgs_zhanbao.llm import LLMError
from sgs_zhanbao.proofread import default_corrected_path, default_corrections_path


class TestFixTitle:
    @pytest.mark.parametrize(
        "title,date,expected",
        [
            ("赛事速递 | 11.5 SGL主播赛战报", "2026-11-06", "赛事速递 | 11.6 SGL主播赛战报"),
            ("SGL主播赛战报", "2026-11-06", "赛事速递 | 11.6 SGL主播赛战报"),
            ("赛事速递 | 11.6 SGL主播赛战报", "2026-11-06", "赛事速递 | 11.6 SGL主播赛战报"),
            ("赛事速递 | 11.5 战报", "2026-01-02", "赛事速递 | 1.2 战报"),
        ],
    )
    def test_normalises(self, title: str, date: str, expected: str):
        assert _fix_title(title, date) == expected

    def test_empty_date_passthrough(self):
        assert _fix_title("某标题", "") == "某标题"

    def test_empty_title_passthrough(self):
        assert _fix_title("", "2026-11-05") == ""

    def test_bad_date_passthrough(self):
        assert _fix_title("某标题", "20261105") == "某标题"

    def test_only_date_no_rest(self):
        assert _fix_title("赛事速递 |", "2026-11-05") == "赛事速递 | 11.5 战报"


class TestResolveCorrected:
    def test_prefers_corrected(self, tmp_path: Path):
        src = tmp_path / "a.txt"
        src.write_text("x", encoding="utf-8")
        corr = default_corrected_path(src)
        corr.write_text("[00:00:01] 校正后", encoding="utf-8")
        assert resolve_corrected(src) == corr

    def test_falls_back_to_source(self, tmp_path: Path):
        src = tmp_path / "a.txt"
        src.write_text("x", encoding="utf-8")
        assert resolve_corrected(src) == src

    def test_explicit_missing_falls_back(self, tmp_path: Path):
        src = tmp_path / "a.txt"
        src.write_text("x", encoding="utf-8")
        assert resolve_corrected(src, tmp_path / "nope.txt") == src

    def test_explicit_present(self, tmp_path: Path):
        src = tmp_path / "a.txt"
        src.write_text("x", encoding="utf-8")
        other = tmp_path / "b.txt"
        other.write_text("[00:00:01] 手工校正", encoding="utf-8")
        assert resolve_corrected(src, other) == other


class TestExtract:
    def _report(self, **over):
        base = {
            "title": "SGL主播赛战报",
            "matches": [
                {"index": 1, "team_a": "开机大队", "score_a": 14, "score_b": 10, "team_b": "败者食尘"}
            ],
            "spotlights": [{"title": "反叫4分", "body": "丈八开杀"}],
            "moments": [{"title": "丈八", "body": "直接拿下"}],
            "bidding": {"team_a": "开机大队：3分起步"},
            "highlights": [{"label": "最强地主", "detail": "魔孙权"}],
            "tomorrow": "",
            "meta": {"event": "SGL"},
        }
        base.update(over)
        return base

    def test_injects_date_and_fixes_title(self, tencent_txt: Path, fake_llm):
        client = fake_llm([self._report()])
        report = extract(tencent_txt, "开机大队 14:10 败者食尘", client=client, log=lambda *_: None)
        assert report.date == "2026-11-05"
        assert report.title == "赛事速递 | 11.5 SGL主播赛战报"
        assert report.meta["usage"]["calls"] == 1

    def test_explicit_date_wins(self, tencent_txt: Path, fake_llm):
        client = fake_llm([self._report()])
        report = extract(tencent_txt, client=client, date="2026-12-01", log=lambda *_: None)
        assert report.date == "2026-12-01"
        assert "12.1" in report.title

    def test_short_transcript_skips_summary(self, tencent_txt: Path, fake_llm):
        """短稿只该调一次 LLM（不做分块摘要）。"""
        client = fake_llm([self._report()])
        extract(tencent_txt, client=client, log=lambda *_: None)
        assert len(client.calls) == 1

    def test_long_transcript_summarises(self, tmp_path: Path, fake_llm):
        p = tmp_path / "20261105120000-long.txt"
        p.write_text(
            "\n".join(f"[00:0{i % 10}:00] " + "很长的解说内容" * 100 for i in range(20)),
            encoding="utf-8",
        )
        from sgs_zhanbao.ingest import load_transcript

        n_chunks = len(load_transcript(p)["chunks"])
        assert n_chunks > 1, "这段测试稿必须切成多块"

        # 每块一次摘要 + 1 次最终提取
        client = fake_llm(["摘要"] * n_chunks + [self._report()])
        extract(p, client=client, log=lambda *_: None)
        assert len(client.calls) == n_chunks + 1

    def test_non_dict_result_raises(self, tencent_txt: Path, fake_llm):
        client = fake_llm(["[1, 2, 3]"])
        with pytest.raises(LLMError, match="不是 JSON 对象"):
            extract(tencent_txt, client=client, log=lambda *_: None)

    def test_scoring_source_in_prompt(self, tencent_txt: Path, fake_llm):
        client = fake_llm([self._report()])
        extract(tencent_txt, "开机大队 14:10 败者食尘", client=client, log=lambda *_: None)
        prompt = client.calls[0][-1]["content"]
        assert "开机大队 14:10 败者食尘" in prompt
        assert "2026-11-05" in prompt

    def test_proofread_meta_attached(self, tencent_txt: Path, fake_llm):
        corr = default_corrections_path(tencent_txt)
        corr.write_text(
            json.dumps(
                {
                    "n_chunks": 3,
                    "failed_chunks": [],
                    "corrections": [
                        {"wrong": "a", "right": "b", "applied": 1},
                        {"wrong": "c", "right": "d", "applied": 0},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        client = fake_llm([self._report()])
        report = extract(tencent_txt, client=client, log=lambda *_: None)
        pf = report.meta["proofread"]
        assert pf["applied"] == 1
        assert pf["pending"] == 1
        assert pf["chunks"] == 3

    def test_no_proofread_meta_when_absent(self, tencent_txt: Path, fake_llm):
        report = extract(tencent_txt, client=fake_llm([self._report()]), log=lambda *_: None)
        assert "proofread" not in report.meta

    def test_empty_result_raises(self, tencent_txt: Path, fake_llm):
        client = fake_llm([{"title": "", "meta": {}}])
        with pytest.raises(LLMError, match="抽取结果为空"):
            extract(tencent_txt, client=client, log=lambda *_: None)

    def test_uses_corrected_transcript(self, tencent_txt: Path, fake_llm):
        default_corrected_path(tencent_txt).write_text(
            "[00:00:05] 校正后的独家内容\n", encoding="utf-8"
        )
        client = fake_llm([self._report()])
        report = extract(tencent_txt, client=client, log=lambda *_: None)
        assert report.meta["source"].endswith(".corrected.txt")
        assert "校正后的独家内容" in client.calls[0][-1]["content"]
