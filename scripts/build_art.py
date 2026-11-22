#!/usr/bin/env python
"""脚本入口：重建版式切片（等价于 sgs-zhanbao-build-art）。

保留 scripts/ 目录是为了让「克隆仓库 → python scripts/build_art.py」也走得通，
不用先 pip install 就能重建素材。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sgs_zhanbao.build_art import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
