"""生成一套**纯代码绘制**的默认版式素材（head / mid / tail）。

为什么需要它
------------
本仓库默认自带的版式素材是从三国杀官方宣传图衍生的，版权不在我们手上，
不能随 MIT 代码一起分发。所以仓库里**只提交这套代码生成的中性素材**：
粉白渐变 + 双线花框 + 云海收口，没有任何第三方美术、没有角色立绘。

它和真实素材遵守同一套约束，因此渲染器不用改一行：

* 三件套都是 928 宽
* ``mid[0]`` 逐像素等于 ``head[-1]``，``mid[-1]`` 逐像素等于 ``tail[0]`` → 接缝 0 色差
* ``mid`` 列内恒定 → 纵向拉伸几千像素也不会出竖条纹

换成自己的素材
--------------
::

    # 方式一：直接改这套默认素材的配色
    python scripts/gen_default_art.py --primary "#E8547C" --paper "#FDF6F4"

    # 方式二：用自己的三张源图重建（见 scripts/build_art.py）
    python scripts/build_art.py --src 我的源图目录 --dst src/sgs_zhanbao/data/art/v2

    # 方式三：不动包，运行时指过去
    set SGS_ART=D:\\我的素材      # 目录里放 head.png / mid.png / tail.png

用法::

    python scripts/gen_default_art.py                 # 写到包内默认位置
    python scripts/gen_default_art.py --preview out.png
"""

from __future__ import annotations

import argparse
import sys
from contextlib import suppress
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# 本文件位于 src/sgs_zhanbao/gen_default_art.py → 上溯三级才是仓库根
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DST = Path(__file__).resolve().parent / "data" / "art" / "v2"

W = 928
H_HEAD = 830
H_MID = 239
H_TAIL = 556

# 默认配色：与 pipeline 的 sgl-pink 主题一致
DEFAULT_PRIMARY = (232, 84, 124)     # 珊瑚红（边框主线）
DEFAULT_PAPER = (253, 246, 244)      # 纸面
DEFAULT_EDGE = (250, 226, 232)       # 外沿浅粉
DEFAULT_DEEP = (196, 61, 99)         # 深红（收口）


def _hex(s: str) -> tuple[int, int, int]:
    s = s.strip().lstrip("#")
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise argparse.ArgumentTypeError(f"颜色要写成 #RRGGBB，收到：{s}")
    return tuple(int(s[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def column_profile(
    primary: tuple[int, int, int],
    paper: tuple[int, int, int],
    edge: tuple[int, int, int],
) -> np.ndarray:
    """逐列颜色剖面 (W, 3)：外沿浅粉 → 珊瑚红线 → 白色高光 → 纸面。

    这条剖面同时是 mid 的全部内容（列内恒定），也是 head 末行、tail 首行，
    三件套因此天然对齐。左右严格镜像，且主线落在 x=24..28 与 W-28..W-24，
    和 head 的立柱坐标一一对应。
    """
    ref = np.tile(np.array(paper, dtype=np.float32), (W, 1))
    hi = np.array([255, 255, 255], dtype=np.float32)

    def band(x0: int, x1: int, color) -> None:
        ref[x0:x1] = np.array(color, dtype=np.float32)

    for mirror in (False, True):
        # 左侧：[a, b)；右侧镜像：[W-b, W-a)，两种情况都保证 x0 < x1
        for a, b, color in (
            (0, 24, edge),        # 外沿
            (24, 28, primary),    # 主线
            (28, 31, hi),         # 高光
            (31, 40, edge),       # 内带
        ):
            x0 = W - b if mirror else a
            x1 = W - a if mirror else b
            band(x0, x1, color)
    return ref


def _emblem(d: ImageDraw.ImageDraw, cx: int, cy: int, primary, edge, paper) -> None:
    """顶部抽象徽记：同心圆 + 菱形花瓣（纯几何，不含任何第三方图形）。"""
    for r, alpha in ((96, 40), (72, 70), (48, 255)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r],
                  outline=(*primary, alpha), width=3)
    for k in range(4):
        ang = np.pi / 4 + k * np.pi / 2
        px, py = cx + int(64 * np.cos(ang)), cy + int(64 * np.sin(ang))
        d.polygon([(px, py - 22), (px + 16, py), (px, py + 22), (px - 16, py)],
                  fill=(*edge, 210), outline=(*primary, 200))
    d.ellipse([cx - 26, cy - 26, cx + 26, cy + 26], fill=(*paper, 255),
              outline=(*primary, 255), width=4)
    d.ellipse([cx - 10, cy - 10, cx + 10, cy + 10], fill=(*primary, 230))


def _petal_row(
    img: Image.Image, positions, primary, paper, radius: int = 26
) -> None:
    """五瓣花形装饰（纯几何：五个圆 + 花心），散布在面板开口与记者牌之间。"""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for cx, cy, scale in positions:
        r = radius * scale
        for k in range(5):
            ang = -np.pi / 2 + k * 2 * np.pi / 5
            px = cx + int(r * 0.92 * np.cos(ang))
            py = cy + int(r * 0.92 * np.sin(ang))
            d.ellipse([px - r * 0.55, py - r * 0.55, px + r * 0.55, py + r * 0.55],
                      fill=(*primary, 26))
        d.ellipse([cx - r * 0.3, cy - r * 0.3, cx + r * 0.3, cy + r * 0.3],
                  fill=(*paper, 120))
    img.alpha_composite(overlay)


def _soft_ornaments(img: Image.Image, y0: int, y1: int, color, n: int = 9) -> None:
    """在 head 顶部画一排半透明圆点装饰（纯几何，不含任何第三方图形）。"""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    span = y1 - y0
    for i in range(n):
        cx = int(W * (i + 0.5) / n)
        cy = y0 + int(span * (0.5 + 0.42 * np.sin(i * 1.7)))
        r = 10 + (i % 3) * 6
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, 46))
    img.alpha_composite(overlay)


def _rounded_plate(
    d: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    w: int,
    h: int,
    *,
    fill,
    border,
    radius: int,
    width: int = 3,
) -> None:
    x0, x1 = cx - w // 2, cx + w // 2
    y0, y1 = cy - h // 2, cy + h // 2
    d.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill, outline=border, width=width)


def make_head(ref: np.ndarray, primary, edge, paper, deep) -> np.ndarray:
    """固定头部。

    三段结构（与渲染器的坐标锚点对应）：

    * 顶部装饰带（0 ~ 240）：柔和圆点 + 收边线
    * 记者牌（中心 BYLINE_CY=674）：HTML 会把署名压在这块牌面上
    * 标题底衬带（TITLE_TOP=782 起）：更浅的一条，让 48px 标题压字更清楚
    """
    head = np.zeros((H_HEAD, W, 3), dtype=np.float32)
    top = np.array(paper, dtype=np.float32) * 0.94 + np.array(primary, dtype=np.float32) * 0.06
    for y in range(H_HEAD):
        f = (y / (H_HEAD - 1)) ** 1.6          # 越接近末行越快贴合剖面
        head[y] = top * (1 - f) + ref * f

    im = Image.fromarray(np.clip(np.rint(head), 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    d = ImageDraw.Draw(im)

    # 左右立柱：与 mid 的边框位置严格对齐（x=24..28 / W-28..W-24）
    for x0, x1 in ((24, 28), (W - 28, W - 24)):
        d.rectangle([x0, 0, x1, H_HEAD], fill=(*primary, 255))
    d.rectangle([0, 0, W, 6], fill=(*edge, 255))

    # 面板上沿：一条双线 + 两端花角方块，让 head/mid 交界处像「纸面面板的开口」
    d.rectangle([28, 300, W - 28, 304], fill=(*primary, 200))
    d.rectangle([28, 310, W - 28, 312], fill=(*edge, 255))
    for cx in (60, W - 60):
        d.rounded_rectangle([cx - 22, 288, cx + 22, 324], radius=10,
                            fill=(*paper, 255), outline=(*primary, 230), width=3)

    _soft_ornaments(im, 40, 200, primary)
    _petal_row(
        im,
        [(150, 430, 1.0), (330, 470, 0.7), (560, 425, 0.85), (770, 470, 0.65),
         (240, 560, 0.6), (690, 560, 0.75)],
        primary, paper,
    )
    d = ImageDraw.Draw(im)          # 装饰层合成后要重新拿画笔

    # 顶部徽记 + 两侧细线，把最上面那段空白用起来
    _emblem(d, 464, 150, primary, edge, paper)
    for x0, x1 in ((150, 372), (556, 778)):
        d.rectangle([x0, 149, x1, 151], fill=(*primary, 120))
        d.ellipse([x0 - 6, 144, x0 + 6, 156], fill=(*edge, 255))
        d.ellipse([x1 - 6, 144, x1 + 6, 156], fill=(*edge, 255))

    # 记者牌（署名压在这里）
    _rounded_plate(d, 464, 674, 660, 92, fill=(255, 253, 250, 235),
                   border=(*primary, 255), radius=46, width=4)
    d.rounded_rectangle([464 - 322, 674 - 38, 464 + 322, 674 + 38], radius=32,
                        outline=(*edge, 255), width=2)

    # 标题底衬带：一条更浅的横向留白，避免标题直接压在渐变上；
    # 上沿压一道深红细线，作为「面板开口 → 正文」的视觉分界
    d.rectangle([28, 756, W - 28, 830], fill=(255, 255, 255, 90))
    d.rectangle([28, 756, W - 28, 759], fill=(*deep, 150))

    out = np.array(im.convert("RGB")).astype(np.float32)
    out[-1] = ref                              # 剖面必须逐像素等于 mid 首行
    return out


def make_tail(ref: np.ndarray, primary, deep, paper) -> np.ndarray:
    """固定尾部：首行等于剖面，向下渐深，底部一条收口线（文字卡片压在上面）。"""
    tail = np.zeros((H_TAIL, W, 3), dtype=np.float32)
    bottom = np.array(paper, dtype=np.float32) * 0.86 + np.array(deep, dtype=np.float32) * 0.14
    for y in range(H_TAIL):
        f = (y / (H_TAIL - 1)) ** 1.4
        tail[y] = ref * (1 - f) + bottom * f

    im = Image.fromarray(np.clip(np.rint(tail), 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    d = ImageDraw.Draw(im)
    # 云海示意：几条越往下越宽的柔和高光线
    for i, yy in enumerate(range(160, H_TAIL - 60, 46)):
        amp = 18 + i * 5
        xs = np.linspace(0, W, 240)
        ys = yy + amp * np.sin(xs / W * np.pi * 3 + i)
        pts = [(int(a), int(b)) for a, b in zip(xs, ys)]
        d.line(pts, fill=(255, 255, 255, 70), width=3)
    d.rectangle([24, H_TAIL - 30, W - 24, H_TAIL - 26], fill=(*primary, 210))
    d.rectangle([0, H_TAIL - 4, W, H_TAIL], fill=(*deep, 255))

    out = np.array(im.convert("RGBA"))[:, :, :3].astype(np.float32)
    out[0] = ref                               # 剖面必须逐像素等于 mid 末行
    rgba = np.concatenate([out, np.full((H_TAIL, W, 1), 255.0, np.float32)], axis=2)
    return rgba


def generate(dst: Path, primary, paper, edge, deep) -> int:
    ref = column_profile(primary, paper, edge)
    head = make_head(ref, primary, edge, paper, deep)
    mid = np.repeat(ref[None, :, :], H_MID, axis=0)
    mid[0] = head[-1]
    mid[-1] = ref
    tail = make_tail(ref, primary, deep, paper)
    mid[-1] = tail[0][:, :3]

    d0 = float(np.abs(mid[0] - head[-1]).mean())
    d1 = float(np.abs(mid[-1] - tail[0][:, :3]).mean())
    col_std = float(mid.std(axis=0).mean())
    print(f"head {W}x{H_HEAD}  mid {W}x{H_MID}  tail {W}x{H_TAIL}")
    print(f"  首行 vs 头部末行 : {d0:.6f}")
    print(f"  末行 vs 尾部首行 : {d1:.6f}")
    print(f"  列内 std        : {col_std:.4f}   (0 = 没有竖条纹)")
    if d0 > 1e-4 or d1 > 1e-4 or col_std > 1e-4:
        print("✗ 约束未满足，已中止")
        return 1

    dst.mkdir(parents=True, exist_ok=True)
    Image.fromarray(head.astype(np.uint8)).save(dst / "head.png", optimize=True)
    Image.fromarray(np.clip(np.rint(mid), 0, 255).astype(np.uint8)).save(
        dst / "mid.png", optimize=True
    )
    Image.fromarray(np.clip(np.rint(tail), 0, 255).astype(np.uint8), "RGBA").save(
        dst / "tail.png", optimize=True
    )
    print(f"✅ 已写入：{dst}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成代码绘制的默认版式素材")
    ap.add_argument("--dst", default=str(DEFAULT_DST))
    ap.add_argument("--primary", type=_hex, default=DEFAULT_PRIMARY, help="#RRGGBB 主线色")
    ap.add_argument("--paper", type=_hex, default=DEFAULT_PAPER, help="#RRGGBB 纸面色")
    ap.add_argument("--edge", type=_hex, default=DEFAULT_EDGE, help="#RRGGBB 外沿色")
    ap.add_argument("--deep", type=_hex, default=DEFAULT_DEEP, help="#RRGGBB 收口色")
    ap.add_argument("--preview", default="", help="额外导出一张三件套拼接的预览图")
    a = ap.parse_args(argv)

    with suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")

    rc = generate(Path(a.dst).expanduser().resolve(), a.primary, a.paper, a.edge, a.deep)
    if rc == 0 and a.preview:
        d = Path(a.dst)
        head, mid, tail = (Image.open(d / f"{n}.png").convert("RGB") for n in ("head", "mid", "tail"))
        canvas = Image.new("RGB", (W, head.height + 400 + tail.height), (255, 255, 255))
        canvas.paste(head, (0, 0))
        canvas.paste(mid.resize((W, 400)), (0, head.height))
        canvas.paste(tail, (0, head.height + 400))
        out = Path(a.preview)
        canvas.save(out)
        print(f"   预览：{out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
