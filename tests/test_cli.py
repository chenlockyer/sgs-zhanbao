"""CLI 冒烟测试：参数解析、退出码、产物落盘（LLM 全部打桩）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sgs_zhanbao.cli import build_parser, main


class TestParser:
    def test_requires_subcommand(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_run_defaults(self):
        a = build_parser().parse_args(["run", "--transcript", "x.txt"])
        assert a.transcript == "x.txt"
        assert a.reuse is False
        assert a.limit == 0

    def test_render_requires_data(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["render"])

    def test_version_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(["--version"])
        assert exc.value.code == 0
        assert "sgs-zhanbao" in capsys.readouterr().out


class TestDoctor:
    def test_runs_and_reports(self, capsys):
        code = main(["doctor"])
        out = capsys.readouterr().out
        assert "sgs-zhanbao" in out
        assert "[版式素材]" in out
        assert "[提示词 / 术语表]" in out
        assert code in (0, 1)


class TestRenderCommand:
    def test_renders_json(self, report_json: Path, tmp_path: Path, capsys):
        from conftest import browser_launchable

        if not browser_launchable():
            pytest.skip("需要可启动的 chromium（pip install '.[render]' 后 playwright install chromium）")
        out = tmp_path / "cli.png"
        code = main(["render", "--data", str(report_json), "--out", str(out)])
        assert code == 0
        assert out.is_file()
        assert "已出图" in capsys.readouterr().out

    def test_missing_data_reports_error(self, tmp_path: Path, capsys):
        code = main(["render", "--data", str(tmp_path / "nope.json"), "--out", str(tmp_path / "o.png")])
        assert code == 1
        assert "✗" in capsys.readouterr().err


class TestExtractCommand:
    def test_writes_report_json(self, tencent_txt: Path, tmp_path: Path, monkeypatch, capsys):
        from sgs_zhanbao import extract as extract_mod

        def fake_extract(*a, **kw):
            from sgs_zhanbao.models import ReportData

            r = ReportData.from_dict(
                {
                    "title": "赛事速递 | 11.5 SGL主播赛战报",
                    "matches": [{"index": 1, "team_a": "A", "score_a": 1, "score_b": 0, "team_b": "B"}],
                    "date": "2026-11-05",
                }
            )
            return r

        monkeypatch.setattr(extract_mod, "extract", fake_extract)
        out = tmp_path / "out" / "report.json"
        code = main([
            "extract", "--transcript", str(tencent_txt),
            "--out", str(out), "--date", "2026-11-05",
        ])
        assert code == 0
        assert out.is_file()
        assert json.loads(out.read_text(encoding="utf-8"))["matches"][0]["team_a"] == "A"
        assert "提取结果概览" in capsys.readouterr().out


class TestRunCommand:
    def test_missing_transcript_exit_2(self, tmp_path: Path, capsys):
        code = main(["run", "--transcript", str(tmp_path / "nope.txt")])
        assert code == 2
        assert "找不到转写稿" in capsys.readouterr().out

    def test_full_run_with_stubs(self, tencent_txt: Path, tmp_path: Path, monkeypatch, capsys):
        from sgs_zhanbao import extract as extract_mod
        from sgs_zhanbao import proofread as proofread_mod
        from sgs_zhanbao import render as render_mod
        from sgs_zhanbao.models import ReportData

        called = {"proofread": 0, "extract": 0, "render": 0}

        def fake_proofread(path, **kw):
            called["proofread"] += 1
            src = Path(path)
            corr = proofread_mod.default_corrected_path(src)
            corr.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            cj = proofread_mod.default_corrections_path(src)
            cj.write_text(json.dumps({"n_chunks": 1, "corrections": []}), encoding="utf-8")
            return proofread_mod.ProofreadResult(
                source=src, corrected_path=corr, corrections_path=cj, n_chunks=1
            )

        def fake_extract(*a, **kw):
            called["extract"] += 1
            return ReportData.from_dict(
                {
                    "title": "SGL战报",
                    "date": kw.get("date") or "2026-11-05",
                    "matches": [{"index": 1, "team_a": "A", "score_a": 1, "score_b": 0, "team_b": "B"}],
                }
            )

        def fake_render(report, output_path=None, **kw):
            called["render"] += 1
            p = Path(output_path or tmp_path / "out.png")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"PNG")
            return p

        monkeypatch.setattr(proofread_mod, "proofread", fake_proofread)
        monkeypatch.setattr(extract_mod, "extract", fake_extract)
        monkeypatch.setattr(render_mod, "render_report", fake_render)

        code = main([
            "run", "--transcript", str(tencent_txt),
            "--root", str(tmp_path), "--date", "2026-11-05",
        ])
        out = capsys.readouterr().out
        assert code == 0, out
        assert called == {"proofread": 1, "extract": 1, "render": 1}
        assert "全部完成" in out
        assert (tmp_path / "data" / "2026-11-05" / "report.json").is_file()

    def test_reuse_skips_proofread(self, tencent_txt: Path, tmp_path: Path, monkeypatch):
        from sgs_zhanbao import extract as extract_mod
        from sgs_zhanbao import proofread as proofread_mod
        from sgs_zhanbao import render as render_mod
        from sgs_zhanbao.models import ReportData

        # 预置校正稿 + 清单，--reuse 应当直接跳过校对
        proofread_mod.default_corrected_path(tencent_txt).write_text("x", encoding="utf-8")
        proofread_mod.default_corrections_path(tencent_txt).write_text("{}", encoding="utf-8")

        called = {"proofread": 0}

        def fake_proofread(*a, **kw):
            called["proofread"] += 1
            raise AssertionError("--reuse 不该再跑校对")

        monkeypatch.setattr(proofread_mod, "proofread", fake_proofread)
        monkeypatch.setattr(
            extract_mod, "extract",
            lambda *a, **kw: ReportData.from_dict({"title": "t", "date": "2026-11-05",
                                                   "matches": [{"team_a": "A", "score_a": 1}]}),
        )
        monkeypatch.setattr(
            render_mod, "render_report",
            lambda r, output_path=None, **kw: (Path(output_path) if output_path
                                               else tmp_path / "o.png"),
        )
        code = main([
            "run", "--transcript", str(tencent_txt),
            "--root", str(tmp_path), "--reuse",
        ])
        assert code == 0
        assert called["proofread"] == 0

    def test_no_proofread_skips(self, tencent_txt: Path, tmp_path: Path, monkeypatch):
        from sgs_zhanbao import extract as extract_mod
        from sgs_zhanbao import proofread as proofread_mod
        from sgs_zhanbao import render as render_mod
        from sgs_zhanbao.models import ReportData

        monkeypatch.setattr(
            proofread_mod, "proofread",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不该跑校对")),
        )
        monkeypatch.setattr(
            extract_mod, "extract",
            lambda *a, **kw: ReportData.from_dict({"title": "t", "date": "2026-11-05",
                                                   "matches": [{"team_a": "A", "score_a": 1}]}),
        )
        monkeypatch.setattr(
            render_mod, "render_report",
            lambda r, output_path=None, **kw: tmp_path / "o.png",
        )
        assert main([
            "run", "--transcript", str(tencent_txt),
            "--root", str(tmp_path), "--no-proofread",
        ]) == 0


class TestErrorSurface:
    def test_runtime_error_is_caught(self, monkeypatch, capsys):
        from sgs_zhanbao import render as render_mod

        monkeypatch.setattr(
            render_mod, "render_report",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("模拟失败")),
        )
        code = main(["render", "--data", "x.json"])
        assert code == 1
        assert "模拟失败" in capsys.readouterr().err
