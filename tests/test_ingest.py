"""转写稿解析与切块。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sgs_zhanbao.ingest import (
    Segment,
    clean_text,
    detect_format,
    guess_date,
    load_transcript,
    merge_segments,
    parse_transcript,
    write_transcript,
)


class TestDetectFormat:
    def test_tencent(self, tencent_txt: Path):
        assert detect_format(tencent_txt.read_text(encoding="utf-8")) == "tencent"

    def test_bracket(self, bracket_txt: Path):
        assert detect_format(bracket_txt.read_text(encoding="utf-8")) == "bracket"

    def test_plain(self, plain_txt: Path):
        assert detect_format(plain_txt.read_text(encoding="utf-8")) == "plain"


class TestParseTranscript:
    def test_tencent_fields(self, tencent_txt: Path):
        segs = parse_transcript(tencent_txt)
        assert len(segs) == 5
        assert segs[0].timestamp == "00:00:05"
        assert segs[0].speaker == "解说甲"
        assert "SGL主播赛" in segs[0].text
        # prefix 必须原样保留，写回时格式才和腾讯会议导出一致
        assert segs[0].prefix.endswith("(00:00:05): ")

    def test_bracket_fields(self, bracket_txt: Path):
        segs = parse_transcript(bracket_txt)
        assert len(segs) == 3
        assert segs[0].timestamp == "00:00:05"
        assert segs[0].text.startswith("欢迎来到")

    def test_plain_lines(self, plain_txt: Path):
        segs = parse_transcript(plain_txt)
        assert len(segs) == 2
        assert segs[0].timestamp == ""

    def test_continuation_line_joins_previous(self, tmp_path: Path):
        p = tmp_path / "broken.txt"
        p.write_text(
            "解说甲 共享音频(00:00:05): 第一句被断行了\n"
            "这里是续行\n"
            "解说甲 共享音频(00:00:09): 第二句\n",
            encoding="utf-8",
        )
        segs = parse_transcript(p)
        assert len(segs) == 2
        assert "这里是续行" in segs[0].text

    def test_short_timestamp_normalised(self, tmp_path: Path):
        p = tmp_path / "short.txt"
        p.write_text("[1:05] 一分钟零五秒\n", encoding="utf-8")
        segs = parse_transcript(p)
        assert segs[0].timestamp == "00:01:05"

    def test_bom_is_ignored(self, tmp_path: Path):
        p = tmp_path / "bom.txt"
        p.write_text("\ufeff[00:00:01] 带 BOM 的文件\n", encoding="utf-8")
        segs = parse_transcript(p)
        assert len(segs) == 1
        assert segs[0].text.startswith("带 BOM")

    def test_empty_file_raises(self, tmp_path: Path):
        p = tmp_path / "empty.txt"
        p.write_text("\n\n", encoding="utf-8")
        with pytest.raises(ValueError, match="没有解析出任何内容"):
            load_transcript(p)

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_transcript(tmp_path / "nope.txt")


class TestSegment:
    @pytest.mark.parametrize(
        "ts,expected",
        [("00:00:05", 5), ("00:01:00", 60), ("01:00:00", 3600), ("", 0), ("1:05", 65)],
    )
    def test_seconds(self, ts: str, expected: int):
        assert Segment(timestamp=ts, speaker="", text="").seconds == expected

    def test_render_keeps_prefix(self):
        s = Segment(timestamp="00:00:05", speaker="甲", text="内容", prefix="甲(00:00:05): ")
        assert s.render() == "甲(00:00:05): 内容"

    def test_render_without_prefix(self):
        s = Segment(timestamp="00:00:05", speaker="甲", text="内容")
        assert s.render(keep_prefix=False) == "[00:00:05] 内容"


class TestWriteTranscript:
    def test_roundtrip_tencent(self, tencent_txt: Path, tmp_path: Path):
        segs = parse_transcript(tencent_txt)
        out = write_transcript(segs, tmp_path / "out.txt", style="tencent")
        again = parse_transcript(out)
        assert [s.timestamp for s in again] == [s.timestamp for s in segs]
        assert [s.text for s in again] == [s.text for s in segs]

    def test_roundtrip_bracket(self, bracket_txt: Path, tmp_path: Path):
        segs = parse_transcript(bracket_txt)
        out = write_transcript(segs, tmp_path / "out.txt", style="bracket")
        assert out.read_text(encoding="utf-8").startswith("[00:00:05] 欢迎来到")

    def test_auto_uses_prefix_when_present(self, tencent_txt: Path, tmp_path: Path):
        segs = parse_transcript(tencent_txt)
        out = write_transcript(segs, tmp_path / "out.txt", style="auto")
        assert "共享音频(00:00:05)" in out.read_text(encoding="utf-8")


class TestCleanText:
    def test_collapses_whitespace(self):
        assert clean_text("早上   好") == "早上 好"

    def test_removes_fillers(self):
        assert clean_text("嗯，这个牌啊，很强") == "这个牌，很强"

    def test_dedupes_punctuation(self):
        assert clean_text("真的吗，，，，") == "真的吗"

    def test_keeps_memes(self):
        """玩梗不能被清洗掉——这是设计约束，不是巧合。"""
        for meme in ("曹敢三", "路边一条", "开机猴", "丈八"):
            assert meme in clean_text(f"嗯，{meme}啊")
            assert clean_text(f"嗯，{meme}啊").find(meme) >= 0


class TestMergeSegments:
    def test_respects_max_chars(self):
        segs = [
            Segment(timestamp=f"00:00:{i:02d}", speaker="", text="很长的一句话" * 10)
            for i in range(20)
        ]
        chunks = merge_segments(segs, max_chars=200)
        assert len(chunks) > 1
        for c in chunks:
            # 单条 piece 本身可能超限，但每块只允许一条超限 piece
            assert c.count("\n") <= 1 or len(c) <= 220

    def test_keeps_timestamp_prefix(self):
        segs = [Segment(timestamp="00:01:00", speaker="", text="一句话")]
        assert merge_segments(segs) == ["[00:01:00] 一句话"]

    def test_drops_empty_segments(self):
        segs = [
            Segment(timestamp="00:00:01", speaker="", text="嗯，"),
            Segment(timestamp="00:00:02", speaker="", text="有内容"),
        ]
        assert merge_segments(segs) == ["[00:00:02] 有内容"]


class TestGuessDate:
    def test_from_tencent_filename(self):
        assert guess_date("voice2text/20261105192559-转写.txt") == "2026-11-05"

    def test_no_date(self):
        assert guess_date("随便一个名字.txt") == ""


class TestLoadTranscript:
    def test_stats(self, tencent_txt: Path):
        tr = load_transcript(tencent_txt)
        assert tr["n_segments"] == 5
        assert tr["format"] == "tencent"
        assert tr["total_chars"] > 0
        assert tr["speakers"] == ["解说甲"]
        assert tr["duration"] == "00:03:00"
