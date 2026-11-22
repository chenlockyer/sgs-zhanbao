"""渲染：HTML 组装（快）+ 真实出图（需要 chromium，慢）。"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from conftest import requires_browser
from sgs_zhanbao.config import Config
from sgs_zhanbao.models import ReportData
from sgs_zhanbao.render import (
    PAPER,
    build_css,
    build_html,
    esc,
    render_report,
)


@pytest.fixture
def report(report_dict: dict) -> ReportData:
    return ReportData.from_dict(report_dict)


class TestEsc:
    def test_escapes_html(self):
        assert esc("<b>&") == "&lt;b&gt;&amp;"

    def test_none_is_empty(self):
        assert esc(None) == ""

    def test_chinese_untouched(self):
        assert esc("开机大队") == "开机大队"


class TestBuildCss:
    def test_contains_seam_prevention(self):
        css = build_css("AAA", "文字")
        assert "display: flow-root" in css, "BFC 是防接缝的第一道保险"
        assert "background-size: 100% 100%" in css
        assert PAPER.lower() in css.lower()

    def test_font_face_when_font_given(self, pkg_root: Path):
        font = Config().font()
        if not font:
            pytest.skip("本机没有楷体")
        css = build_css("AAA", "开机大队", font)
        assert "@font-face" in css
        assert "base64," in css

    def test_no_font_face_when_missing(self, tmp_path: Path):
        css = build_css("AAA", "文字", tmp_path / "nope.ttf")
        assert "@font-face" not in css
        assert "KaiTi" in css, "仍要有系统字体兜底"


class TestBuildHtml:
    def test_is_standalone(self, report: ReportData):
        html = build_html(report)
        assert html.startswith("<!DOCTYPE html>")
        # 所有素材内联，单文件可离线打开
        assert html.count("data:image/png;base64,") == 3
        assert "<link" not in html
        assert "http://" not in html and "https://" not in html

    def test_contains_sections(self, report: ReportData):
        html = build_html(report)
        for text in ("焦点局", "名场面", "叫分策略", "当日看点总结", "明日预告"):
            assert text in html

    def test_results_rendered(self, report: ReportData):
        html = build_html(report)
        assert "开机大队" in html
        assert "14" in html and "10" in html

    def test_single_match_has_no_index_label(self, report: ReportData):
        html = build_html(report)
        assert "第1局：" not in html

    def test_multi_match_has_index_label(self, report_dict: dict):
        report_dict["matches"] = [
            {"index": 1, "team_a": "A", "score_a": 5, "score_b": 1, "team_b": "B"},
            {"index": 2, "team_a": "C", "score_a": 1, "score_b": 5, "team_b": "D"},
        ]
        html = build_html(ReportData.from_dict(report_dict))
        assert "第1局：" in html and "第2局：" in html

    def test_bidding_label_split(self, report: ReportData):
        """「开机大队：3分起步」要拆成 加粗队名 + 正文，不能再套一层。"""
        html = build_html(report)
        assert "<b>开机大队：</b>3分起步的激进策略" in html

    def test_winner_line(self, report: ReportData):
        assert "恭喜 开机大队 获得本场比赛的胜利" in build_html(report)

    def test_escapes_injected_html(self, report_dict: dict):
        report_dict["spotlights"] = [{"title": "<script>", "body": "x"}]
        html = build_html(ReportData.from_dict(report_dict))
        assert "<script>" not in html.split("<script>")[0] or "&lt;script&gt;" in html

    def test_accepts_plain_dict(self, report_dict: dict):
        assert "开机大队" in build_html(report_dict)

    def test_accepts_json_path(self, report_json: Path):
        assert "开机大队" in build_html(report_json)

    def test_optional_sections_omitted_cleanly(self):
        html = build_html({"title": "只有标题", "matches": []})
        assert "焦点局" not in html
        assert "恭喜" not in html

    def test_reporter_override(self, report: ReportData):
        cfg = Config()
        cfg.render.reporter = "战报记者：测试"
        assert "战报记者：测试" in build_html(report, cfg=cfg)


class TestMissingArt:
    def test_clear_error_message(self, report: ReportData, monkeypatch, tmp_path: Path):
        import sgs_zhanbao.render as render_mod

        # 打桩 art_dir(cfg=None)：返回一个空目录
        monkeypatch.setattr(render_mod, "art_dir", lambda cfg=None: tmp_path)
        with pytest.raises(FileNotFoundError, match="缺少版式素材"):
            build_html(report)

    def test_env_override(self, tmp_path: Path, monkeypatch):
        """SGS_ART 指到自己的目录时，应从那里读素材。"""
        monkeypatch.setenv("SGS_ART", str(tmp_path))
        from sgs_zhanbao.config import Config

        assert Config().art_dir() == tmp_path


@requires_browser
class TestRenderPng:
    def test_renders_png(self, report: ReportData, tmp_path: Path):
        out = render_report(report, output_path=tmp_path / "out.png")
        assert out.is_file()
        assert out.stat().st_size > 50_000

        from PIL import Image

        with Image.open(out) as im:
            # 2x 出图，宽度应是设计稿 928 的两倍
            assert im.width == 1856
            assert im.height > 1000

    def test_html_sidecar_written(self, report: ReportData, tmp_path: Path):
        out = render_report(report, output_path=tmp_path / "out.png")
        assert out.with_suffix(".html").is_file()

    def test_longer_content_grows_the_image(self, report_dict: dict, tmp_path: Path):
        short = render_report(ReportData.from_dict(report_dict), output_path=tmp_path / "s.png")
        report_dict["spotlights"] = [
            {"title": f"标题{i}", "body": "很长的正文内容。" * 12} for i in range(8)
        ]
        long = render_report(ReportData.from_dict(report_dict), output_path=tmp_path / "l.png")

        from PIL import Image

        with Image.open(short) as a, Image.open(long) as b:
            assert b.height > a.height

    def test_scale_one(self, report: ReportData, tmp_path: Path):
        out = render_report(report, output_path=tmp_path / "1x.png", scale=1.0)
        from PIL import Image

        with Image.open(out) as im:
            assert im.width == 928

    def test_no_seam_band_in_output(self, report: ReportData, tmp_path: Path):
        """正文与尾图接缝处不应出现页底色横线。

        做法：正文区左右两侧应当是连续的花框竖线（不该被截断）。
        接缝一旦漏出，左右两条竖线会同时断掉——这比逐行查颜色可靠，
        因为正文纸面本身也接近页底色。
        """
        out = render_report(report, output_path=tmp_path / "seam.png")
        from PIL import Image

        with Image.open(out) as im:
            rgb = im.convert("RGB")
            w, h = rgb.size
            # 左侧花框内缘（设计稿 x≈30，2x 后 ≈60）应当始终是彩色（非纯白）
            x = round(w * 0.032)
            gaps = []
            for y in range(int(h * 0.35), int(h * 0.75)):
                r, g, b = rgb.getpixel((x, y))
                if r > 250 and g > 250 and b > 250:
                    gaps.append(y)
            # 允许极少数行（花角留白），但不能出现连续断层
            assert len(gaps) < 20, f"左侧花框有 {len(gaps)} 行空白，疑似接缝"

    def test_busy_target_falls_back(self, report: ReportData, tmp_path: Path, monkeypatch):
        """目标文件被占用时应另存，而不是崩掉。

        真实场景：看图软件占着 png，原地重试 24 次（约 12 秒）都换不掉。
        """
        import sgs_zhanbao.render as render_mod

        target = tmp_path / "busy.png"
        real_replace = os.replace   # 先抓住真身，否则打桩会递归

        def busy_only_for_target(src, dst):
            # 只拦「落到目标名」，允许改名另存——那才是要验证的兜底路径
            if Path(dst) == target:
                raise OSError("文件被占用")
            return real_replace(src, dst)

        monkeypatch.setattr(render_mod.os, "replace", busy_only_for_target)
        monkeypatch.setattr(render_mod.time, "sleep", lambda *_: None)
        out = render_report(report, output_path=target)
        assert out.is_file()
        assert re.search(r"busy_\d{6}\.png$", out.name), f"应另存为带时间戳的文件，实际 {out.name}"

    def test_unwritable_target_keeps_tmp(self, report: ReportData, tmp_path: Path, monkeypatch):
        """连改名都失败时，至少把临时图留下并把原因说清楚。"""
        import sgs_zhanbao.render as render_mod

        def always_busy(src, dst):
            raise OSError("全部被占用")

        monkeypatch.setattr(render_mod.os, "replace", always_busy)
        monkeypatch.setattr(render_mod.time, "sleep", lambda *_: None)
        with pytest.raises(RuntimeError, match="无法写入"):
            render_report(report, output_path=tmp_path / "busy.png")
        # 已经渲染好的临时图不该被删掉（错误信息里会指向它）
        assert list(tmp_path.glob("busy.tmp-*.png"))
