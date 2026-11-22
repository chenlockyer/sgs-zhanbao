"""仓库自带的 examples/ 必须真的能用（README 的第一条命令就是它）。"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import requires_browser
from sgs_zhanbao.ingest import load_transcript
from sgs_zhanbao.models import ReportData
from sgs_zhanbao.render import build_html

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


class TestBundledExamples:
    def test_files_exist(self):
        assert (EXAMPLES / "sample_transcript.txt").is_file()
        assert (EXAMPLES / "sample_report.json").is_file()

    def test_transcript_parses(self):
        tr = load_transcript(EXAMPLES / "sample_transcript.txt")
        assert tr["format"] == "tencent"
        assert tr["n_segments"] >= 10
        assert tr["speakers"], "示例稿应该解析出发言人"
        assert len(tr["chunks"]) >= 1

    def test_report_loads_and_is_renderable(self):
        raw = json.loads((EXAMPLES / "sample_report.json").read_text(encoding="utf-8"))
        report = ReportData.from_dict(raw)
        assert not report.is_empty()
        html = build_html(report)
        assert "焦点局" in html
        assert "<!DOCTYPE html>" in html

    @requires_browser
    def test_render_example(self, tmp_path: Path):
        from sgs_zhanbao.render import render_report

        out = render_report(
            EXAMPLES / "sample_report.json", output_path=tmp_path / "example.png"
        )
        assert out.is_file()
        assert out.stat().st_size > 50_000

    def test_example_png_committed(self):
        """README 顶部引用的成品图必须在仓库里（否则 README 显示破图）。"""
        p = EXAMPLES / "example.png"
        assert p.is_file(), "跑 `sgs-zhanbao render --data examples/sample_report.json` 生成"
        assert p.stat().st_size > 50_000

    def test_example_html_not_committed(self):
        """渲染会顺手写 .html 边车文件，那种产物不该进仓库。"""
        assert not (EXAMPLES / "sample_report.html").exists()
