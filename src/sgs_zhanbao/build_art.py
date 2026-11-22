"""从三张源图生成渲染用的版式切片（head / mid / tail）。

输入（默认找 ``./assets/source``，也可 ``--src`` 指定）：:

    头部.png            928 x 1043 以上   上：插画 + 品牌字 + 记者牌 + 面板上沿
    中部可复用部分.png   928 x 179          中部条的正宗素材（浅色干净边框 + 纸面）
    尾部.png            928 x 556          云海 + 花徽收口

输出（``--dst``，默认包内 ``data/art/v2``）：:

    head.png   928 x 830    固定头部
    mid.png    928 x 239    可纵向拉伸的中部条
    tail.png   928 x 556    固定尾部（RGBA，全不透明）

中部条构造（决定「边框颜色对不对」和「有没有竖条纹」）
------------------------------------------------------
* 剖面取自 ``中部可复用部分.png`` 的**逐列中位数**：这是那条正确的浅色边框
  （外沿浅粉 → 一条珊瑚红线 → 白色高光 → 浅粉内带 → 纸面），列内恒定，
  所以拉伸几千像素也不会出现竖条纹。
* 头部末段 / 尾部首段向这个剖面渐变（``SEAM_ROWS`` 行），保证
  首行 == 头部末行、末行 == 尾部首行（逐像素相等 → 接缝 0 色差），
  同时把头部花角装饰下沿那道偏深的阴影带自然过渡掉。

验收标准（脚本会打印，不达标直接返回非 0）::

    首行 vs 头部末行 : 0.000000
    末行 vs 尾部首行 : 0.000000
    列内 std        : 0.0000

用法::

    python scripts/build_art.py
    python scripts/build_art.py --src assets/source --dst src/sgs_zhanbao/data/art/v2
"""

from __future__ import annotations

import argparse
import sys
from contextlib import suppress
from pathlib import Path

import numpy as np
from PIL import Image

# Windows 控制台默认 GBK，强制 UTF-8 避免打印中文/符号报错
with suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "assets" / "source"
DEFAULT_DST = ROOT / "src" / "sgs_zhanbao" / "data" / "art" / "v2"

SRC_NAMES = {
    "head": ("头部.png", "head.png"),
    "mid": ("中部可复用部分.png", "mid.png"),
    "tail": ("尾部.png", "tail.png"),
}

H_HEAD = 830        # 头部裁切高度：完整保留面板上沿（标题会压在内阴影带上，不裁素材）
BAND_H = 239        # 中部条高度（会被拉伸，此值只影响渐变细腻度）
SEAM_ROWS = 48      # 头部末段 / 尾部首段向中部剖面过渡的行数
W_EXPECT = 928      # 设计稿宽度：三张源图必须一致


def _load(path: Path) -> np.ndarray:
    if not path.is_file():
        raise FileNotFoundError(f"找不到素材：{path}")
    return np.array(Image.open(path).convert("RGB")).astype(np.float32)


def build(src_dir: Path, dst_dir: Path) -> int:
    head_src = src_dir / SRC_NAMES["head"][0]
    mid_src = src_dir / SRC_NAMES["mid"][0]
    tail_src = src_dir / SRC_NAMES["tail"][0]

    head_full = _load(head_src)
    strip = _load(mid_src)
    tail_full = _load(tail_src)

    widths = {head_full.shape[1], strip.shape[1], tail_full.shape[1]}
    if widths != {W_EXPECT}:
        print(f"⚠ 三张源图宽度应一致且为 {W_EXPECT}，实际：{sorted(widths)}")
        print("  版式坐标是按 928 宽设计稿写死的；宽度不符请先缩放到 928 宽。")
        return 1

    if head_full.shape[0] < H_HEAD:
        print(f"✗ 头图高度不足：需要 ≥{H_HEAD}，实际 {head_full.shape[0]}")
        return 1

    head = head_full[:H_HEAD].copy()
    tail = tail_full.copy()

    # ---- 中部条剖面：逐列中位数（去掉纸面细纹理，列内恒定 → 拉伸无竖条纹）
    ref = np.median(strip, axis=0)  # (W, 3)

    # ---- 头部末段 / 尾部首段向 ref 过渡（f=1 时正好等于 ref）
    for i in range(SEAM_ROWS):
        f = (SEAM_ROWS - i) / SEAM_ROWS
        y_head = H_HEAD - 1 - i
        head[y_head] = head[y_head] * (1 - f) + ref * f
        tail[i] = tail[i] * (1 - f) + ref * f

    # ---- 中部条：整条就是 ref（列内恒定），首末行强制对齐头/尾
    band = np.repeat(ref[None, :, :], BAND_H, axis=0)
    band[0] = head[-1]
    band[-1] = tail[0]

    # ---- 尾部 alpha：全不透明（尾图完整保留，文字卡片压在上面）
    h, w = tail.shape[:2]
    alpha = np.full((h, w, 1), 255.0, dtype=np.float32)
    tail_rgba = np.concatenate([tail, alpha], axis=2)

    # ---- 校验
    ok = True
    d0 = float(np.abs(band[0] - head[-1]).mean())
    d1 = float(np.abs(band[-1] - tail[0]).mean())
    col_std = float(band.std(axis=0).mean())

    print(f"头部   {head.shape[1]}x{head.shape[0]}   (源高 {head_full.shape[0]})")
    print(f"中部条 {band.shape[1]}x{band.shape[0]}   ← 剖面取自 {mid_src.name}")
    print(f"尾部   {tail.shape[1]}x{tail.shape[0]}")
    print(f"  首行 vs 头部末行 : {d0:.6f}")
    print(f"  末行 vs 尾部首行 : {d1:.6f}")
    print(f"  列内 std        : {col_std:.4f}   (0 = 完全没有竖条纹)")

    if d0 > 1e-4 or d1 > 1e-4:
        print("✗ 接缝未对齐，检查裁切点")
        ok = False
    if col_std > 1e-4:
        print("✗ 中部条不是列内恒定，拉伸后会出现竖条纹")
        ok = False
    if not ok:
        return 1

    dst_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(head.astype(np.uint8)).save(dst_dir / "head.png", optimize=True)
    Image.fromarray(np.clip(np.rint(band), 0, 255).astype(np.uint8)).save(
        dst_dir / "mid.png", optimize=True
    )
    Image.fromarray(np.clip(np.rint(tail_rgba), 0, 255).astype(np.uint8), "RGBA").save(
        dst_dir / "tail.png", optimize=True
    )

    print(f"\n✅ 已生成：{dst_dir}")
    for name in ("head", "mid", "tail"):
        p = dst_dir / f"{name}.png"
        im = Image.open(p)
        print(f"   {name}.png  {im.width}x{im.height}  {p.stat().st_size // 1024} KB")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从三张源图生成版式切片")
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="源图目录")
    ap.add_argument("--dst", default=str(DEFAULT_DST), help="输出目录")
    a = ap.parse_args(argv)
    try:
        return build(Path(a.src).expanduser().resolve(), Path(a.dst).expanduser().resolve())
    except FileNotFoundError as exc:
        print(f"✗ {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
