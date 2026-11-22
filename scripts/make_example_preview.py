"""生成 README 用的示例成品图（缩小 + 压缩）。

仓库里提交的 ``examples/example.png`` 是给 README 看的预览，不是成品规格：
成品 1856px 宽、5MB+，塞进仓库既浪费又拖慢 clone。所以这里固定输出
720px 宽的预览，并把体积压到 1MB 以下。

改了版式或默认素材之后重跑一次，README 的图才会跟着变：

    python scripts/make_example_preview.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402

from sgs_zhanbao.render import render_report  # noqa: E402

PREVIEW_WIDTH = 720
MAX_BYTES = 1_000_000


def main() -> int:
    data = ROOT / "examples" / "sample_report.json"
    out = ROOT / "examples" / "example.png"
    tmp = ROOT / "output" / "_example_full.png"

    full = render_report(data, output_path=tmp)
    with Image.open(full) as im:
        h = round(im.height * PREVIEW_WIDTH / im.width)
        preview = im.convert("RGB").resize((PREVIEW_WIDTH, h), Image.LANCZOS)

    # 先按 optimize 存；还超预算就降色深（长图观感几乎不变）
    preview.save(out, optimize=True)
    if out.stat().st_size > MAX_BYTES:
        preview.convert("P", palette=Image.ADAPTIVE, colors=128).save(out, optimize=True)

    size = out.stat().st_size
    print(f"成品（不提交）：{full}  {full.stat().st_size // 1024} KB")
    print(f"预览（提交）  ：{out}  {PREVIEW_WIDTH}x{preview.height}  {size // 1024} KB")
    if size > MAX_BYTES:
        print("⚠ 预览仍超过 1MB，README 加载会偏慢")
        return 1

    # 顺手清掉渲染留下的 html 边车
    sidecar = full.with_suffix(".html")
    if sidecar.exists():
        sidecar.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
