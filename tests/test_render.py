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
        """接缝处绝不能漏出一整行页底色（那就是画面上的一条横线）。

        判据不猜「哪一列是花框」——不同素材边框位置不一样。它直接查缝隙的
        特征签名：漏出来的那一行**整行都是页底色**，连花框立柱的位置也是；
        正常行总有边框/装饰偏离页底色。实测正常 ≤0.002、注入 1px 缝 =1.000，
        所以这个阈值有极大余量。

        渲染时 ``_fix_seam()`` 已经从布局层保证间距为 0，这里是第二道防线：
        万一将来 CSS 改动让 BFC 挡不住塌陷，这条测试会红。
        """
        out = render_report(report, output_path=tmp_path / "seam.png")
        import numpy as np
        from PIL import Image

        from sgs_zhanbao.config import Config
        from sgs_zhanbao.render import _png_size, art_dir

        art = art_dir()
        head_h = _png_size(art / "head.png")[1]
        tail_h = _png_size(art / "tail.png")[1]
        # 页底色来自配置，不能写死（换肤后写死的值会失效）
        paper = np.array(
            [int(Config().palette.paper.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)],
            dtype=np.float32,
        )

        with Image.open(out) as im:
            a = np.asarray(im.convert("RGB"), dtype=np.float32)
        w = a.shape[1]
        scale = w / 928.0
        seams = {
            "头→正文": int(round(head_h * scale)),
            "正文→尾": a.shape[0] - int(round(tail_h * scale)),
        }

        def paper_frac(y: int) -> float:
            """该行里「等于页底色」的像素占比（去掉两侧边缘抗锯齿）。"""
            row = a[y, 8 : w - 8]
            return float((np.abs(row - paper).max(axis=1) <= 1).mean())

        # 这条断言自带可验证性：要通过，交界附近每一行都必须有 ≥95% 的像素
        # **不等于**页底色（花框立柱、装饰线就是这些像素）。
        # 也就是说「判据能识别缝隙」是「测试通过」的必要条件 —— 如果素材在
        # 交界处本来就是一片页底色，worst 会直接接近 1.0，测试立刻红，
        # 而不是空过。实测默认素材：正常 ≈0.002，人为注入 1px 缝 =1.000。
        for name, y in seams.items():
            worst = max(paper_frac(j) for j in range(y - 2, y + 3))
            assert worst < 0.05, (
                f"{name} 交界第 {y} 行附近整行页底色占比 {worst:.3f}，"
                f"画面会有一条横贯的横线"
            )

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
