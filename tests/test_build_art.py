"""素材重建脚本：三张切片必须满足接缝 0 色差、列内恒定。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PIL")

from sgs_zhanbao.build_art import BAND_H, H_HEAD, SEAM_ROWS, build  # noqa: E402

W = 928


def _make_sources(tmp_path: Path) -> Path:
    """造三张合成源图：宽度对，颜色有纵向渐变，便于检验接缝算法。"""
    from PIL import Image

    src = tmp_path / "src"
    src.mkdir()

    # 头部：上 2/3 深粉，最后几行暗（模拟花角阴影），共 H_HEAD+100 高
    head = np.zeros((H_HEAD + 100, W, 3), dtype=np.uint8)
    head[:, :, 0] = 250
    head[:, :, 1] = 180
    head[:, :, 2] = 200
    head[-80:, :, :] = (200, 90, 110)
    Image.fromarray(head).save(src / "头部.png")

    # 中部：列内恒定、带一段深红竖线（模拟边框）
    mid = np.zeros((179, W, 3), dtype=np.uint8)
    mid[:, :, :] = (255, 245, 245)
    mid[:, 60:66, :] = (230, 60, 90)
    Image.fromarray(mid).save(src / "中部可复用部分.png")

    # 尾部：上 80 行暗（模拟渐隐），其余云海色
    tail = np.zeros((556, W, 3), dtype=np.uint8)
    tail[:, :, :] = (245, 240, 250)
    tail[:80, :, :] = (210, 120, 140)
    Image.fromarray(tail).save(src / "尾部.png")
    return src


class TestBuildArt:
    def test_outputs_and_seams(self, tmp_path: Path, capsys):
        src = _make_sources(tmp_path)
        dst = tmp_path / "out"
        assert build(src, dst) == 0

        from PIL import Image

        head = np.array(Image.open(dst / "head.png").convert("RGB")).astype(np.float32)
        mid = np.array(Image.open(dst / "mid.png").convert("RGB")).astype(np.float32)
        tail = np.array(Image.open(dst / "tail.png").convert("RGB")).astype(np.float32)

        assert head.shape == (H_HEAD, W, 3)
        assert mid.shape == (BAND_H, W, 3)
        assert tail.shape[0] == 556

        # 接缝 0 色差
        assert float(np.abs(mid[0] - head[-1]).mean()) < 1e-4
        assert float(np.abs(mid[-1] - tail[0]).mean()) < 1e-4
        # 列内恒定 → 拉伸无竖条纹
        assert float(mid.std(axis=0).mean()) < 1e-4

    def test_seam_rows_faded(self, tmp_path: Path):
        """头部末段应向剖面渐变，把深色阴影带过渡掉。"""
        src = _make_sources(tmp_path)
        dst = tmp_path / "out"
        build(src, dst)

        from PIL import Image

        head = np.array(Image.open(dst / "head.png").convert("RGB")).astype(np.float32)
        # 倒数第 SEAM_ROWS 行应已明显变亮（不再是原来的深色）
        faded = head[-SEAM_ROWS]
        assert faded.mean() > 200

    def test_wrong_width_rejected(self, tmp_path: Path, capsys):
        src = _make_sources(tmp_path)
        from PIL import Image

        Image.new("RGB", (800, 900), (255, 255, 255)).save(src / "头部.png")
        assert build(src, tmp_path / "out") == 1
        assert "宽度应一致" in capsys.readouterr().out

    def test_head_too_short_rejected(self, tmp_path: Path, capsys):
        src = _make_sources(tmp_path)
        from PIL import Image

        Image.new("RGB", (W, 400), (255, 255, 255)).save(src / "头部.png")
        assert build(src, tmp_path / "out") == 1
        assert "头图高度不足" in capsys.readouterr().out

    def test_missing_source_reports(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="找不到素材"):
            build(tmp_path / "empty", tmp_path / "out")

    def test_packaged_art_matches_contract(self):
        """仓库里已提交的三张切片必须满足同一套约束（防止手改坏）。"""
        from PIL import Image

        from sgs_zhanbao.config import RES

        head = np.array(Image.open(RES.art / "head.png").convert("RGB")).astype(np.float32)
        mid = np.array(Image.open(RES.art / "mid.png").convert("RGB")).astype(np.float32)
        tail = np.array(Image.open(RES.art / "tail.png").convert("RGB")).astype(np.float32)

        assert head.shape[1] == W and mid.shape[1] == W and tail.shape[1] == W
        assert float(np.abs(mid[0] - head[-1]).mean()) < 0.5
        assert float(np.abs(mid[-1] - tail[0]).mean()) < 0.5
        assert float(mid.std(axis=0).mean()) < 0.5
