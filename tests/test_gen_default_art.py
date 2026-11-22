"""默认版式素材生成器：三件套必须满足与真实素材相同的约束。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PIL")

from sgs_zhanbao.gen_default_art import (  # noqa: E402
    H_HEAD,
    H_MID,
    H_TAIL,
    W,
    column_profile,
    generate,
    main,
)


class TestColumnProfile:
    def test_width_and_dtype(self):
        ref = column_profile((232, 84, 124), (253, 246, 244), (250, 226, 232))
        assert ref.shape == (W, 3)
        assert ref.dtype == np.float32

    def test_border_at_expected_columns(self):
        """边框必须落在渲染器假设的位置（x=24..28 与右侧对称）。"""
        primary = (232, 84, 124)
        ref = column_profile(primary, (253, 246, 244), (250, 226, 232))
        assert tuple(int(v) for v in ref[26]) == primary
        assert tuple(int(v) for v in ref[W - 26]) == primary

    def test_paper_in_the_middle(self):
        paper = (253, 246, 244)
        ref = column_profile((232, 84, 124), paper, (250, 226, 232))
        assert tuple(int(v) for v in ref[W // 2]) == paper


class TestGenerate:
    def test_constraints_hold(self, tmp_path: Path, capsys):
        assert generate(tmp_path, (232, 84, 124), (253, 246, 244),
                        (250, 226, 232), (196, 61, 99)) == 0

        from PIL import Image

        head = np.array(Image.open(tmp_path / "head.png").convert("RGB")).astype(np.float32)
        mid = np.array(Image.open(tmp_path / "mid.png").convert("RGB")).astype(np.float32)
        tail = np.array(Image.open(tmp_path / "tail.png")).astype(np.float32)

        assert head.shape == (H_HEAD, W, 3)
        assert mid.shape == (H_MID, W, 3)
        assert tail.shape == (H_TAIL, W, 4)

        # 接缝 0 色差
        assert float(np.abs(mid[0] - head[-1]).mean()) < 1e-4
        assert float(np.abs(mid[-1] - tail[0][:, :3]).mean()) < 1e-4
        # 列内恒定 → 拉伸无竖条纹
        assert float(mid.std(axis=0).mean()) < 1e-4

    def test_deterministic(self, tmp_path: Path):
        """同一套参数两次生成必须字节级一致（CI 可复现）。"""
        a, b = tmp_path / "a", tmp_path / "b"
        args = ((232, 84, 124), (253, 246, 244), (250, 226, 232), (196, 61, 99))
        generate(a, *args)
        generate(b, *args)
        for name in ("head.png", "mid.png", "tail.png"):
            assert (a / name).read_bytes() == (b / name).read_bytes()

    def test_custom_colors_change_output(self, tmp_path: Path):
        pink, blue = tmp_path / "pink", tmp_path / "blue"
        generate(pink, (232, 84, 124), (253, 246, 244), (250, 226, 232), (196, 61, 99))
        generate(blue, (60, 120, 220), (244, 248, 253), (220, 232, 250), (40, 90, 170))
        from PIL import Image

        a = np.array(Image.open(pink / "mid.png").convert("RGB"))
        b = np.array(Image.open(blue / "mid.png").convert("RGB"))
        assert not np.array_equal(a, b)
        # 换色后约束仍成立
        assert float(np.abs(a.astype(np.float32).std(axis=0)).mean()) < 1e-4

    def test_cli_accepts_hex(self, tmp_path: Path):
        rc = main(["--dst", str(tmp_path / "out"), "--primary", "#3C78DC"])
        assert rc == 0
        assert (tmp_path / "out" / "head.png").is_file()

    def test_cli_rejects_bad_hex(self, tmp_path: Path):
        with pytest.raises(SystemExit):
            main(["--dst", str(tmp_path), "--primary", "红色"])


class TestPackagedDefault:
    def test_shipped_art_satisfies_contract(self):
        """仓库自带的那份必须已经按当前脚本生成过（防止改了脚本忘了重生成）。"""
        from PIL import Image

        from sgs_zhanbao.config import RES

        head = np.array(Image.open(RES.art / "head.png").convert("RGB")).astype(np.float32)
        mid = np.array(Image.open(RES.art / "mid.png").convert("RGB")).astype(np.float32)
        tail = np.array(Image.open(RES.art / "tail.png")).astype(np.float32)

        assert head.shape[1] == W and mid.shape[1] == W and tail.shape[1] == W
        assert float(np.abs(mid[0] - head[-1]).mean()) < 1e-4
        assert float(np.abs(mid[-1] - tail[0][:, :3]).mean()) < 1e-4
        assert float(mid.std(axis=0).mean()) < 1e-4

    def test_art_is_script_generated(self):
        """默认素材必须由脚本生成：与重新生成的结果字节一致。

        这条同时保证两件事：素材是原创可分发的（不是借用的美术），
        以及改了 gen_default_art.py 之后不会忘了重生成提交。
        """
        import tempfile

        from sgs_zhanbao.config import RES
        from sgs_zhanbao.gen_default_art import (
            DEFAULT_DEEP,
            DEFAULT_EDGE,
            DEFAULT_PAPER,
            DEFAULT_PRIMARY,
        )

        with tempfile.TemporaryDirectory() as td:
            generate(Path(td), DEFAULT_PRIMARY, DEFAULT_PAPER, DEFAULT_EDGE, DEFAULT_DEEP)
            for name in ("head.png", "mid.png", "tail.png"):
                assert (Path(td) / name).read_bytes() == (RES.art / name).read_bytes(), (
                    f"{name} 与脚本输出不一致：请跑 `python -m sgs_zhanbao.gen_default_art` 重生成"
                )
