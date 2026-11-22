"""sgs-zhanbao —— 三国杀赛事战报生成工作流。

把一场赛事直播的转写稿，自动变成一张能直接发的战报长图::

    转写稿 txt ──► 文字校对 ──► 结构化提取 ──► HTML/CSS 渲染 ──► PNG 长图

三步各自独立、可单独运行、产物可人工修改：

* :mod:`sgs_zhanbao.proofread`  只修语音转文字错字（独立上下文，唯一的纠错入口）
* :mod:`sgs_zhanbao.extract`    只做提炼（提示词里没有任何纠错指令）
* :mod:`sgs_zhanbao.render`     HTML/CSS → Playwright 截图 → PNG

快速上手::

    from sgs_zhanbao import extract, proofread, render_report

    proofread.proofread("voice2text/match.txt")
    report = extract.extract("voice2text/match.txt", score_info="A 14:10 B")
    render_report(report, output_path="output/report.png")
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = [
    "__version__",
    "Config",
    "ReportData",
    "extract",
    "proofread",
    "render",
    "render_report",
    "build_html",
]

# 显式导入子模块，让 `from sgs_zhanbao import extract` 这种写法可用
from . import extract, proofread, render  # noqa: E402
from .config import Config  # noqa: E402
from .models import ReportData  # noqa: E402
from .render import build_html, render_report  # noqa: E402
