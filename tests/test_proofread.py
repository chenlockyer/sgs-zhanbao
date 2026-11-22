"""校对：JSON 校验、去重、替换安全性、终审降级、端到端（打桩）。"""

from __future__ import annotations

import json
from pathlib import Path

from sgs_zhanbao.ingest import Segment
from sgs_zhanbao.proofread import (
    Correction,
    ProofreadResult,
    _dedupe,
    _order_for_report,
    _parse_corrections,
    _replace_all,
    apply_corrections,
    default_corrected_path,
    default_corrections_path,
    load_corrections,
    load_glossary,
    proofread,
    render_glossary,
)


class TestParseCorrections:
    CHUNK = "刘焉反叫了四分，摸到丈八蛇矛，杀穿了对面的诸葛亮。"

    def test_accepts_valid(self):
        out = _parse_corrections(
            {"corrections": [
                {"wrong": "丈八蛇矛", "right": "丈八蛇矛", "confidence": "high"}
            ]},
            self.CHUNK,
        )
        assert out == []  # wrong == right 被丢弃

    def test_keeps_real_correction(self):
        out = _parse_corrections(
            {
                "corrections": [
                    {
                        "wrong": "丈八蛇矛",
                        "right": "丈八蛇矛",
                        "confidence": "high",
                    },
                    {
                        "wrong": "杀穿",
                        "right": "杀穿",
                        "confidence": "high",
                    },
                    {
                        "wrong": "刘焉",
                        "right": "刘鄢",
                        "confidence": "medium",
                        "category": "武将名",
                    },
                ]
            },
            self.CHUNK,
        )
        assert len(out) == 1
        assert out[0].wrong == "刘焉"
        assert out[0].confidence == "medium"

    def test_rejects_hallucinated_wrong(self):
        """wrong 必须逐字出现在片段里，否则丢弃。"""
        out = _parse_corrections(
            {"corrections": [{"wrong": "不存在的词", "right": "某个词", "confidence": "high"}]},
            self.CHUNK,
        )
        assert out == []

    def test_rejects_too_long(self):
        long_wrong = "很" * 50
        out = _parse_corrections(
            {"corrections": [{"wrong": long_wrong, "right": "x", "confidence": "high"}]},
            self.CHUNK + long_wrong,
        )
        assert out == []

    def test_normalises_unknown_confidence(self):
        out = _parse_corrections(
            {"corrections": [{"wrong": "刘焉", "right": "刘鄢", "confidence": "极高"}]},
            self.CHUNK,
        )
        assert out[0].confidence == "medium"

    def test_accepts_bare_list(self):
        out = _parse_corrections(
            [{"wrong": "刘焉", "right": "刘鄢", "confidence": "high"}], self.CHUNK
        )
        assert len(out) == 1

    def test_ignores_non_dict_items(self):
        out = _parse_corrections({"corrections": ["刘焉", None, 3]}, self.CHUNK)
        assert out == []


class TestReplaceAll:
    def test_basic(self):
        text, n = _replace_all("刘焉很强，刘焉很稳", "刘焉", "刘鄢")
        assert text == "刘鄢很强，刘鄢很稳"
        assert n == 2

    def test_skips_already_correct(self):
        """「无中生有」不能被「无血」→「无懈」这类规则误伤，也不能自我嵌套。"""
        text, n = _replace_all("无中生有", "无中", "无中生有")
        assert text == "无中生有"
        assert n == 0

    def test_prefix_overlap(self):
        # wrong 是 right 的前缀时，已正确的部分要跳过
        text, n = _replace_all("丈八蛇矛和丈八", "丈八", "丈八蛇矛")
        assert text == "丈八蛇矛和丈八蛇矛"
        assert n == 1

    def test_no_match(self):
        text, n = _replace_all("无关内容", "刘焉", "刘鄢")
        assert text == "无关内容"
        assert n == 0


class TestDedupe:
    def test_same_pair_kept_once(self):
        cs = [
            Correction("丈八", "丈八蛇矛"),
            Correction("丈八", "丈八蛇矛"),
        ]
        assert len(_dedupe(cs, log=lambda *_: None)) == 1

    def test_conflict_keeps_high_confidence(self):
        cs = [
            Correction("刘焉", "刘鄢", confidence="medium"),
            Correction("刘焉", "刘炎", confidence="high"),
        ]
        out = _dedupe(cs, log=lambda *_: None)
        assert len(out) == 1
        assert out[0].right == "刘炎"

    def test_different_wrong_both_kept(self):
        cs = [Correction("丈八", "丈八蛇矛"), Correction("蛇矛", "丈八蛇矛")]
        assert len(_dedupe(cs, log=lambda *_: None)) == 2


class TestApplyCorrections:
    def _segs(self):
        return [
            Segment(timestamp="00:00:01", speaker="甲", text="丈八很强"),
            Segment(timestamp="00:00:02", speaker="甲", text="丈八蛇矛更强"),
        ]

    def test_only_high_confidence_applied(self):
        segs = self._segs()
        cs = [
            Correction("丈八", "丈八蛇矛", confidence="medium"),
            Correction("很强", "超强", confidence="high"),
        ]
        apply_corrections(segs, cs, log=lambda *_: None)
        assert segs[0].text == "丈八超强"
        assert cs[0].applied == 0
        assert cs[1].applied == 1

    def test_longer_wrong_applied_first(self):
        """长词优先：否则「丈八」先替换会把「丈八蛇矛」打散成「丈八蛇矛蛇矛」。"""
        segs = [Segment(timestamp="00:00:01", speaker="", text="丈八蛇矛")]
        cs = [
            Correction("丈八", "丈八蛇矛", confidence="high"),
            Correction("丈八蛇矛", "丈八长矛", confidence="high"),
        ]
        apply_corrections(segs, cs, log=lambda *_: None)
        assert segs[0].text == "丈八长矛"
        assert cs[0].applied == 0, "长词已吃掉全部匹配，短词不该再命中"
        assert cs[1].applied == 1

    def test_no_double_replacement(self):
        """替换结果不能被另一条修正二次改写。"""
        segs = [Segment(timestamp="00:00:01", speaker="", text="曹感三")]
        cs = [
            Correction("曹感三", "曹敢三", confidence="high"),
            Correction("曹敢三", "曹纯", confidence="high"),
        ]
        apply_corrections(segs, cs, log=lambda *_: None)
        assert segs[0].text == "曹敢三"

    def test_timestamp_scoped(self):
        segs = self._segs()
        cs = [Correction("丈八", "丈八蛇矛", ts="00:00:02", confidence="high")]
        apply_corrections(segs, cs, log=lambda *_: None)
        # 第 1 段不匹配该时间戳 → 不动
        assert segs[0].text == "丈八很强"
        # 第 2 段的「丈八」已经是「丈八蛇矛」的一部分 → 不能自我嵌套
        assert segs[1].text == "丈八蛇矛更强"
        assert cs[0].applied == 0

    def test_global_applies_everywhere(self):
        segs = self._segs()
        cs = [Correction("很强", "超强", confidence="high")]
        apply_corrections(segs, cs, log=lambda *_: None)
        # 全局修正在每一段都生效；applied 是总命中数
        assert segs[0].text == "丈八超强"
        assert cs[0].applied == 1
        assert apply_corrections(
            [Segment(timestamp="", speaker="", text="很强很强")],
            [Correction("很强", "超强", confidence="high")],
            log=lambda *_: None,
        )[0].applied == 2

    def test_applied_zero_when_not_found(self):
        segs = [Segment(timestamp="00:00:01", speaker="", text="别的内容")]
        cs = [Correction("刘焉", "刘鄢", confidence="high")]
        apply_corrections(segs, cs, log=lambda *_: None)
        assert cs[0].applied == 0
        assert cs[0].tag() == "未应用"

    def test_already_correct_not_counted(self):
        """原文已是正确写法时不能自我嵌套，且不算「改过」。"""
        segs = [Segment(timestamp="", speaker="", text="无中生有")]
        cs = [Correction("无中", "无中生有", confidence="high")]
        apply_corrections(segs, cs, log=lambda *_: None)
        assert segs[0].text == "无中生有"
        assert cs[0].applied == 0


class TestOrdering:
    def test_sorted_by_first_appearance(self):
        segs = [
            Segment(timestamp="1", speaker="", text="乙在前"),
            Segment(timestamp="2", speaker="", text="甲在后"),
        ]
        cs = [Correction("甲", "x"), Correction("乙", "y")]
        out = _order_for_report(cs, segs)
        assert [c.wrong for c in out] == ["乙", "甲"]


class TestGlossary:
    def test_loads_packaged_glossary(self):
        g = load_glossary()
        assert g["teams"]["开机大队"]
        assert "武将" not in g  # 键名是英文的

    def test_missing_returns_empty(self, tmp_path: Path):
        assert load_glossary(tmp_path / "nope.yaml") == {}

    def test_render_contains_key_sections(self):
        text = render_glossary(load_glossary())
        assert "战队/机构" in text
        assert "武将" in text
        assert "玩梗" in text
        assert "开机猴" in text  # 误写要出现在术语表里

    def test_render_empty(self):
        assert render_glossary({}) == "（无术语表）"


class TestProofreadEndToEnd:
    """端到端：打桩 LLM，验证产物落盘与置信度处理。"""

    def _responses(self):
        return [
            # 第 1 块：两条候选，一条 high 一条 low
            {
                "corrections": [
                    {
                        "wrong": "开机大对",
                        "right": "开机大队",
                        "ts": "",
                        "confidence": "high",
                        "category": "战队名",
                        "note": "同音误识别",
                    },
                    {
                        "wrong": "败者食尘",
                        "right": "败者食辰",
                        "ts": "",
                        "confidence": "low",
                        "category": "战队名",
                        "note": "存疑",
                    },
                ]
            },
            # 终审：保留第一条
            {"verdicts": [{"index": 0, "keep": True, "reason": ""}]},
        ]

    def _transcript(self, tmp_path: Path) -> Path:
        p = tmp_path / "20261105190000-测试.txt"
        p.write_text(
            "解说甲 共享音频(00:00:05): 开机大对今天对阵败者食尘。\n"
            "解说甲 共享音频(00:00:12): 开机大对这边状态不错。\n",
            encoding="utf-8",
        )
        return p

    def test_applies_high_only(self, tmp_path: Path, fake_llm):
        src = self._transcript(tmp_path)
        client = fake_llm(self._responses())
        result = proofread(src, client=client, log=lambda *_: None)

        assert isinstance(result, ProofreadResult)
        assert result.corrected_path.is_file()
        assert result.corrections_path.is_file()

        corrected = result.corrected_path.read_text(encoding="utf-8")
        assert "开机大队今天" in corrected
        assert "开机大队这边" in corrected
        assert "败者食尘" in corrected, "low 置信不该被替换"

        assert result.applied_count == 1
        assert result.pending_count == 1

    def test_corrections_json_schema(self, tmp_path: Path, fake_llm):
        src = self._transcript(tmp_path)
        proofread(src, client=fake_llm(self._responses()), log=lambda *_: None)
        path = default_corrections_path(src)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["source"] == src.name
        assert payload["n_chunks"] >= 1
        assert payload["failed_chunks"] == []
        assert payload["usage"]["calls"] >= 1
        assert any(c["wrong"] == "开机大对" for c in payload["corrections"])

    def test_verify_rejection_downgrades(self, tmp_path: Path, fake_llm):
        """终审否决 → 降级为 low → 不写进逐字稿。"""
        src = self._transcript(tmp_path)
        client = fake_llm([
            {"corrections": [{
                "wrong": "开机大对", "right": "开机大队",
                "confidence": "high", "category": "战队名",
            }]},
            {"verdicts": [{"index": 0, "keep": False, "reason": "测试否决"}]},
        ])
        result = proofread(src, client=client, log=lambda *_: None)
        assert result.applied_count == 0
        corrected = result.corrected_path.read_text(encoding="utf-8")
        assert "开机大对" in corrected
        assert "开机大队" not in corrected
        assert "终审否决" in result.corrections[0].note

    def test_bad_chunk_recorded_not_fatal(self, tmp_path: Path, fake_llm):
        """某一块接口挂了，其余照常出稿，失败块号记录在 failed_chunks。"""
        src = self._transcript(tmp_path)
        client = fake_llm(["完全不是 JSON", "完全不是 JSON", {"verdicts": []}])
        result = proofread(src, client=client, log=lambda *_: None)
        assert result.failed_chunks == [1]
        assert result.corrected_path.is_file()

    def test_limit_only_runs_n_chunks(self, tmp_path: Path, fake_llm):
        src = tmp_path / "long.txt"
        lines = [f"[00:{i:02d}:00] 第{i}句话内容" for i in range(40)]
        src.write_text("\n".join(lines), encoding="utf-8")
        client = fake_llm([{"corrections": []}] + [{"verdicts": []}])
        result = proofread(src, client=client, limit=1, log=lambda *_: None)
        assert result.n_chunks == 1

    def test_summary_text(self):
        r = ProofreadResult(
            source=Path("a.txt"),
            corrected_path=Path("a.corrected.txt"),
            corrections_path=Path("a.corrections.json"),
            corrections=[Correction("a", "b", applied=1), Correction("c", "d")],
            n_chunks=2,
        )
        assert "采信 1 处" in r.summary()
        assert "待确认 1 处" in r.summary()


class TestLoadCorrections:
    def test_missing_returns_empty(self, tmp_path: Path):
        meta, cs = load_corrections(tmp_path / "nope.json")
        assert meta == {} and cs == []

    def test_reads_back(self, tmp_path: Path):
        p = tmp_path / "x.corrections.json"
        p.write_text(
            json.dumps(
                {
                    "n_chunks": 1,
                    "corrections": [
                        {"wrong": "甲", "right": "乙", "confidence": "high", "applied": 2},
                        {"wrong": "", "right": "丙"},  # 空 wrong 丢弃
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        meta, cs = load_corrections(p)
        assert meta["n_chunks"] == 1
        assert len(cs) == 1
        assert cs[0].applied == 2


class TestDefaultPaths:
    def test_paths(self):
        p = Path("voice2text/20261105-x.txt")
        assert default_corrected_path(p).name == "20261105-x.corrected.txt"
        assert default_corrections_path(p).name == "20261105-x.corrections.json"
