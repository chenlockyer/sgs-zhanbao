"""SGL 战报渲染器 v2 —— 固定头 / 拉伸正文 / 固定尾。

版式原理
--------
::

    头图 head.png  928 x 830   固定贴顶（插画 + SGL战报 + 记者牌 + 面板上沿花角）
    中部 mid.png   928 x 239   纵向拉伸填充正文（background-size: 100% 100%）
    尾图 tail.png  928 x 556   固定贴底（云海 + 花徽收口）

中部条怎么做的（决定成败）
--------------------------
正文区要从 239px 拉到上万 px（约 40 倍）。任何「逐列」的细微差异都会被放大成竖条纹。
所以中部条必须满足：

1. 纸面区：每列纵向线性插值（头部末行 → 尾部首行），列内纵向恒定 → 拉伸无竖条纹
2. 边框区：固定为头部末行，保持双线/花角锐利
3. 首行逐像素 == 头部末行，末行逐像素 == 尾部首行 → 两处接缝 0 色差

素材由 ``sgs-zhanbao-build-art`` 从三张源图生成（见 scripts/build_art.py）。

两道接缝保险
------------
``.body`` 底边就是尾图顶边，只要中间漏出 1px，露出来的就是页底色，而且这条缝会横贯
整幅、把左右花框的竖线截断——看上去就是一条横线。所以：

* ``.body { display: flow-root }`` 建立 BFC，挡住最后一个段落的 margin-bottom 塌陷
* 渲染时 ``_fix_seam()`` 实测「头→正文」「正文→尾」两处间距，有缝就补，
  补不齐直接抛错中止出图，不让带缝的图流出去
"""

from __future__ import annotations

import base64
import contextlib
import html
import json
import os
import re
import struct
import time
from pathlib import Path
from typing import Any, Callable

from .config import Config, Palette
from .models import ReportData

# ---------------- 版式参数（928 宽设计稿） ----------------
W = 928

TEXT_L = 96          # 正文左边距
TEXT_R = 84          # 正文右边距
PAD_TOP = 2          # 头部底边 → 正文首行
PAD_BOT = 120        # 正文末行 → 尾部图案（渲染时按底栏实际高度再精调）

TAIL_BOTTOM = 104    # 底部信息区离页面底边距离：让开尾图底部的花徽收口
CARD_GAP = 24        # 底部信息区顶端与正文末行的最小间距

FS_TITLE = 48
FS_SEC = 42
FS_RESULT = 34
FS_BODY = 32
FS_TABLE = 28

LH_BODY = 1.15

TITLE_TOP = 782      # 标题压在头图空白带上的 y：避开面板上沿的花角卷边

BYLINE_CX = 464      # 记者牌中心 x
BYLINE_CY = 674      # 记者牌文字中心 y
BYLINE_W = 660
FS_BYLINE = 44

FONT_FAMILY = "SGLKai"
# 默认页底色。接缝检测与报错信息里出现的色值必须来自 cfg.palette.paper，
# 这个常量只是「没传 palette 时是什么」的兜底，别在 CSS 里直接用它。
PAPER = "#FCEFEE"

DEFAULT_REPORTER = "战报记者：王战答疑团-江南"


# ======================== 兼容层 ========================

def esc(s: object) -> str:
    return html.escape(str(s or ""), quote=False)


def _to_dict(d: Any) -> dict:
    """兼容 ReportData 对象、裸 dict、JSON 路径。"""
    if isinstance(d, (str, Path)):
        return json.loads(Path(d).read_text(encoding="utf-8"))
    if isinstance(d, dict):
        return d
    if hasattr(d, "to_dict"):
        return d.to_dict()
    raise TypeError(f"无法转换的数据类型：{type(d).__name__}")


def _g(d: Any, *keys, default=None):
    """取第一个非空字段（支持 dict / dataclass）。"""
    for k in keys:
        if isinstance(d, dict):
            if k in d and d[k] not in (None, ""):
                return d[k]
        else:
            v = getattr(d, k, None)
            if v not in (None, ""):
                return v
    return default


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ======================== 各区块 ========================

def _results(d: Any) -> str:
    ms = _g(d, "matches", default=[]) or []
    if not ms:
        return ""
    multi = len(ms) > 1
    parts = []
    for m in ms:
        head = f"第{esc(_g(m, 'index', default=''))}局：" if multi else ""
        team_a = esc(_g(m, "team_a", default=""))
        score_a = esc(_g(m, "score_a", default=""))
        score_b = esc(_g(m, "score_b", default=""))
        team_b = esc(_g(m, "team_b", default=""))
        parts.append(
            f'<div class="result">{head}{team_a} '
            f'<span class="score">{score_a} : {score_b}</span> {team_b}</div>'
        )
    return '<div class="results">' + "".join(parts) + "</div>"


def _spotlights(d: Any) -> str:
    items = _g(d, "spotlights", default=[]) or []
    if not items:
        return ""
    body = "".join(
        f'<p class="txt"><b>{esc(_g(s, "title", default=""))}：</b>'
        f'{esc(_g(s, "body", default=""))}</p>'
        for s in items
    )
    return '<div class="sec">焦点局</div>' + body


def _moments(d: Any) -> str:
    items = _g(d, "moments", default=[]) or []
    if not items:
        return ""
    body = "".join(
        f'<div class="item"><span class="dot"></span>'
        f'<b>{esc(_g(m, "title", default=""))}：</b>{esc(_g(m, "body", default=""))}</div>'
        for m in items
    )
    return '<div class="sec">名场面</div>' + body


def _label_and_body(value: str, fallback: str = "") -> tuple[str, str]:
    """把「队名：正文」拆成（标签, 正文）。

    叫分策略的取值本身写成「开机大队：3分起步……」，所以直接拿队名前缀当标签，
    不要再套一层「队伍A：」。
    """
    s = str(value or "").strip()
    for sep in ("：", ":"):
        head, found, tail = s.partition(sep)
        if found and tail.strip() and 0 < len(head.strip()) <= 8:
            return head.strip(), tail.strip()
    return fallback, s


def _bidding(d: Any) -> str:
    b = _g(d, "bidding", default=None)
    if not b:
        return ""
    parts: list[str] = []
    for key in ("team_a", "team_b", "team_c", "team_d"):
        v = str(_g(b, key, default="") or "").strip()
        if not v:
            continue
        name, body = _label_and_body(v)
        if name:
            parts.append(f'<p class="txt"><b>{esc(name)}：</b>{esc(body)}</p>')
        else:
            parts.append(f'<p class="txt">{esc(body)}</p>')

    kr = str(_g(b, "key_round", default="") or "").strip()
    if kr:
        name, body = _label_and_body(kr, fallback="关键局")
        parts.append(f'<p class="txt"><b>{esc(name)}：</b>{esc(body)}</p>')

    if not parts:
        return ""
    return '<div class="sec">叫分策略</div>' + "".join(parts)


def _highlights(d: Any) -> str:
    items = _g(d, "highlights", default=[]) or []
    if not items:
        return ""
    rows = "".join(
        f'<tr><td>{esc(_g(h, "label", default=""))}</td>'
        f'<td>{esc(_g(h, "detail", default=""))}</td></tr>'
        for h in items
    )
    return (
        '<div class="sec">当日看点总结</div>'
        '<table class="tb"><tbody>' + rows + "</tbody></table>"
    )


def _tomorrow(d: Any) -> str:
    t = str(_g(d, "tomorrow", default="") or "").strip()
    if not t:
        return ""
    return f'<div class="sec">明日预告</div><div class="tomorrow">{esc(t)}</div>'


def _win(d: Any) -> str:
    ms = _g(d, "matches", default=[]) or []
    winners: list[str] = []
    for m in ms:
        sa = _int(_g(m, "score_a", default=0))
        sb = _int(_g(m, "score_b", default=0))
        if sa > sb:
            winners.append(str(_g(m, "team_a", default="")))
        elif sb > sa:
            winners.append(str(_g(m, "team_b", default="")))
    winners = [w for w in winners if w.strip()]
    if not winners:
        return ""
    return f'<div class="win">恭喜 {esc(" 和 ".join(winners))} 获得本场比赛的胜利。</div>'


# ======================== 字体 ========================

def _subset_b64(font_path: Path, chars: str) -> str:
    """按用到的字做子集并 base64 编码，避免把 11MB 全字库塞进 HTML。"""
    from io import BytesIO

    from fontTools.subset import Options, Subsetter
    from fontTools.ttLib import TTFont

    opts = Options()
    opts.name_IDs = ["*"]
    opts.name_legacy = True
    opts.name_languages = ["*"]
    opts.layout_features = ["*"]
    opts.notdef_outline = True
    opts.hinting = False
    # 这三张表对 CID 字体没意义，留着只会让 fontTools 打印 "NOT subset" 警告
    opts.drop_tables += ["DSIG", "meta"]

    font = TTFont(str(font_path), fontNumber=0, lazy=True)
    subsetter = Subsetter(options=opts)
    subsetter.populate(text=chars)
    subsetter.subset(font)
    buf = BytesIO()
    font.save(buf)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _font_face(text: str, font_path: Path | None) -> str:
    if not font_path or not Path(font_path).is_file():
        return ""
    try:
        chars = "".join(sorted({c for c in text if not c.isspace()}))
        if not chars:
            return ""
        b64 = _subset_b64(Path(font_path), chars)
        return (
            f'@font-face {{ font-family: "{FONT_FAMILY}"; '
            f"src: url(data:font/ttf;base64,{b64}) "
            'format("truetype"); font-style: normal; font-display: block; }'
        )
    except Exception as exc:  # noqa: BLE001 —— 字体只是观感，失败不阻断出图
        print(f"⚠ 字体子集化失败（改用系统字体）：{exc}")
        return ""


# ======================== CSS ========================

def _rgba(hex_color: str, alpha: float) -> str:
    """把 ``#RRGGBB`` 转成 ``rgba(...)``，用于需要半透明的描边/垫色。"""
    s = hex_color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"颜色要写成 #RRGGBB / #RGB，收到：{hex_color!r}")
    r, g, b = (int(s[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha:g})"


def build_css(
    mid_b64: str,
    text: str = "",
    font_path: Path | None = None,
    palette: Palette | None = None,
) -> str:
    """生成整套 CSS。

    所有颜色都从 ``palette`` 取，不再写死在字符串里 —— 历史上这些色值散在
    CSS 里，改 ``Theme`` 静默无效。默认 Palette 的值与旧 CSS 逐字节一致，
    所以不传 palette 时出图不变。
    """
    c = palette or Palette()
    font_face = _font_face(text, font_path)
    return f"""
{font_face}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

.page {{ position: relative; width: {W}px; margin: 0 auto; }}

body {{
  width: {W}px; margin: 0;
  background: {c.paper};
  font-family: "{FONT_FAMILY}", "KaiTi", "STKaiti", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  color: {c.body};
  font-weight: 700;
  letter-spacing: -0.045em;
  -webkit-font-smoothing: antialiased;
}}

/* ---------- 固定头部 ---------- */
.head {{ position: relative; width: {W}px; line-height: 0; }}
.head img {{ width: 100%; display: block; }}

/* 记者署名（头部米色牌内，动态文字） */
.byline {{
  position: absolute;
  left: {BYLINE_CX - BYLINE_W // 2}px;
  top: {BYLINE_CY}px;
  transform: translateY(-50%);
  width: {BYLINE_W}px;
  text-align: center;
  line-height: 1;
  font-size: {FS_BYLINE}px;
  font-weight: 700;
  color: {c.byline};
  letter-spacing: 0;
}}

/* ---------- 可拉伸正文 ---------- */
.body {{
  position: relative;
  width: {W}px;
  margin-top: 0;
  /* flow-root 建立 BFC：最后一个 p 的 margin-bottom 不会塌陷到 .body 外面，
     否则正文底边与尾图之间会漏出页底色（页面上就是一条横线） */
  display: flow-root;
  background-image: url("data:image/png;base64,{mid_b64}");
  background-repeat: no-repeat;
  background-size: 100% 100%;      /* 单张纵向拉伸，首末行像素精确保留 */
  padding: {PAD_TOP}px {TEXT_R}px {PAD_BOT}px {TEXT_L}px;
}}

/* ---------- 固定尾部 ---------- */
.tail {{ width: {W}px; line-height: 0; }}
.tail img {{ width: 100%; display: block; }}

/* ---------- 正文排版 ---------- */
/* 标题直接压在头图底部那条空白带上（素材不裁，文字绝对定位在素材上，
   和底部信息区压尾图是同一套做法）。 */
.headline {{
  position: absolute; left: 0; right: 0; top: {TITLE_TOP}px;
  text-align: center; font-size: {FS_TITLE}px; font-weight: 700;
  color: {c.headline}; line-height: 1.2; letter-spacing: -0.045em;
  text-wrap: balance; z-index: 6;
}}
.sec {{
  text-align: center; font-size: {FS_SEC}px; font-weight: 700;
  color: {c.deep}; margin: 10px 0 4px; letter-spacing: .04em;
}}
.results {{ margin: 4px 0 2px; }}
.result {{
  font-size: {FS_RESULT}px; font-weight: 700; color: {c.deep};
  text-align: center; line-height: 1.45;
}}
.result .score {{ color: {c.headline}; padding: 0 .10em; font-variant-numeric: tabular-nums; }}

p.txt {{
  font-size: {FS_BODY}px; line-height: {LH_BODY};
  text-align: justify; margin-bottom: 4px;
}}
p.txt b {{ color: {c.deep}; font-weight: 700; }}

.item {{
  position: relative; font-size: {FS_BODY}px; line-height: 1.3;
  text-align: justify; padding-left: 26px; margin-bottom: 6px;
}}
.item .dot {{
  position: absolute; left: 2px; top: .58em;
  width: 8px; height: 8px; border-radius: 50%;
  border: 3px solid {c.dot}; background: #fff;
}}
.item b {{ color: {c.deep}; }}

table.tb {{
  width: 100%; border-collapse: separate; border-spacing: 0;
  border: 2px solid {c.dot}; border-radius: 10px; overflow: hidden;
  font-size: {FS_TABLE}px; margin: 8px 0 12px;
}}
table.tb td {{
  padding: 6px 12px; border-top: 1px solid {c.row_border};
  vertical-align: top; line-height: 1.2;
}}
table.tb tr:nth-child(even) td {{ background: {c.row_even}; }}
table.tb td:first-child {{
  font-weight: 700; color: {c.deep}; white-space: nowrap; width: 24%;
}}

.tomorrow {{
  font-size: 30px; line-height: 1.35; text-align: justify;
  padding: 16px 20px; background: {c.card_bg};
  border: 2px dashed {c.dot}; border-radius: 10px;
}}

.win {{
  text-align: center; font-size: 32px; font-weight: 700;
  color: {c.deep}; margin-top: 10px; letter-spacing: .02em;
}}
/* 底部信息区：压在尾图上，垫一层白色柔边（中间实、四周渐隐）让字更清楚 */
.bottom-card {{
  position: absolute; left: 20px; right: 20px; bottom: {TAIL_BOTTOM}px;
  background: none;
  border: 0;
  padding: 26px 50px 20px;
  z-index: 5;
}}
.bottom-card::before {{
  content: "";
  position: absolute; inset: 0; z-index: -1;
  background: rgba(255, 255, 255, .30);
  border-radius: 24px;
  -webkit-mask-image:
    linear-gradient(to right, transparent 0, #000 56px, #000 calc(100% - 56px), transparent 100%),
    linear-gradient(to bottom, transparent 0, #000 44px, #000 calc(100% - 44px), transparent 100%);
  -webkit-mask-composite: source-in;
  mask-image:
    linear-gradient(to right, transparent 0, #000 56px, #000 calc(100% - 56px), transparent 100%),
    linear-gradient(to bottom, transparent 0, #000 44px, #000 calc(100% - 44px), transparent 100%);
  mask-composite: intersect;
}}
.bottom-card .sec {{ margin: 6px 0 2px; font-size: {FS_SEC}px; color: {c.bottom_sec}; }}
.bottom-card .tomorrow {{
  padding: 0; border: 0; background: transparent;
  font-size: 28px; line-height: 1.3; color: {c.bottom_tomorrow};
}}
.bottom-card .win {{ margin-top: 6px; font-size: 30px; color: {c.bottom_sec}; }}
.bottom-card table.tb {{
  margin: 2px 0 4px; font-size: 30px;
  border: 0;
}}
.bottom-card table.tb td {{
  padding: 3px 10px; line-height: 1.15;
  border-top: 1px solid {_rgba(c.deep, 0.28)};
  background: transparent;
}}
.bottom-card table.tb tr:first-child td {{ border-top: 0; }}
.bottom-card table.tb tr:nth-child(even) td {{ background: transparent; }}
"""


# ======================== 整页 ========================

def art_dir(cfg: Config | None = None) -> Path:
    return (cfg or Config()).art_dir()


def _b64_png(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(
            f"缺少版式素材：{path}\n"
            f"  重新生成：python scripts/build_art.py --src <源图目录> --dst <输出目录>\n"
            f"  换自己的素材：设置环境变量 SGS_ART=<素材目录>（需含 head/mid/tail.png）"
        )
    return base64.b64encode(path.read_bytes()).decode()


def _png_size(path: Path) -> tuple[int, int]:
    """只读 PNG 头拿尺寸，避免为了一个数字引入 Pillow。"""
    head = Path(path).read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"不是 PNG 文件：{path}")
    return struct.unpack(">II", head[16:24])


def _fit_bottom_card(page, tail_png: Path) -> int:
    """底部信息区是绝对定位压在尾图上的，比尾图还高。

    渲染前按实测高度给正文补 ``padding-bottom``，保证信息区顶端不顶到正文末行；
    同时信息区底边留出 ``TAIL_BOTTOM``，让开尾图底部的花徽收口。

    间距推导（页高 = 头 + 正文 + 尾）::

        信息区顶端 - 正文末行 = padding_bottom + 尾高 - TAIL_BOTTOM - 信息区高

    要这个间距 == ``CARD_GAP``，于是::

        padding_bottom = CARD_GAP + TAIL_BOTTOM + 信息区高 - 尾高
    """
    tail_h = _png_size(tail_png)[1]
    card_h = page.evaluate(
        "() => { const c = document.querySelector('.bottom-card');"
        " return c ? Math.ceil(c.getBoundingClientRect().height) : 0; }"
    )
    pad_bottom = max(0, int(CARD_GAP + TAIL_BOTTOM + card_h - tail_h))
    page.evaluate(
        "pad => { document.querySelector('.body').style.paddingBottom = pad + 'px'; }",
        pad_bottom,
    )
    page.wait_for_timeout(120)
    return pad_bottom


_SEAM_JS = """() => {
  const box = el => el.getBoundingClientRect();
  const head = box(document.querySelector('.head'));
  const body = box(document.querySelector('.body'));
  const tail = box(document.querySelector('.tail'));
  return [Math.round(body.top - head.bottom), Math.round(tail.top - body.bottom)];
}"""


def _fix_seam(page, *, paper: str = PAPER, max_rounds: int = 3) -> int:
    """保证「头 / 正文 / 尾」三段素材严丝合缝。

    量到缝隙就用 padding-bottom 补；补不掉就抛错，不让带缝的图流出去。
    ``paper`` 只用于报错信息里说明「会漏出什么颜色」。
    """
    for _ in range(max_rounds):
        head_gap, tail_gap = page.evaluate(_SEAM_JS)
        if head_gap == 0 and tail_gap == 0:
            return 0
        if tail_gap > 0:
            page.evaluate(
                "g => { const b = document.querySelector('.body');"
                " b.style.paddingBottom ="
                " (parseFloat(getComputedStyle(b).paddingBottom) + g) + 'px'; }",
                tail_gap,
            )
        else:
            # 头/正文之间出缝：把正文上边距顶回来
            page.evaluate(
                "g => { const b = document.querySelector('.body');"
                " b.style.marginTop = (parseFloat(getComputedStyle(b).marginTop) + g) + 'px'; }",
                -head_gap,
            )
        page.wait_for_timeout(80)

    left = page.evaluate(_SEAM_JS)
    if left[0] != 0 or left[1] != 0:
        raise RuntimeError(
            f"素材接缝对不齐（头-正文 {left[0]}px，正文-尾 {left[1]}px），"
            f"会露出 {paper} 横条纹，已中止出图"
        )
    return 0


def build_html(data: Any, *, cfg: Config | None = None) -> str:
    """ReportData / dict → 完整 HTML（素材与字体全部内联，单文件可离线打开）。"""
    cfg = cfg or Config()
    d = _to_dict(data)
    art = art_dir(cfg)
    head_b64 = _b64_png(art / "head.png")
    mid_b64 = _b64_png(art / "mid.png")
    tail_b64 = _b64_png(art / "tail.png")

    reporter = _g(d, "reporter", default=None) or cfg.render.reporter or DEFAULT_REPORTER

    sections = "".join([_results(d), _spotlights(d), _moments(d), _bidding(d)])

    body_html = f"""<div class="page">
<div class="head">
  <img src="data:image/png;base64,{head_b64}" alt="">
  <div class="byline">{esc(reporter)}</div>
  <div class="headline">{esc(_g(d, "title", default=""))}</div>
</div>
<div class="body">
  {sections}
</div>
<div class="tail"><img src="data:image/png;base64,{tail_b64}" alt=""></div>
<div class="bottom-card">{_highlights(d)}{_tomorrow(d)}{_win(d)}</div>
</div>
<script>
// 底部信息区压在尾图上、比尾图还高；这里先按实际高度补一次 padding，
// 出图时 render() 还会再量一次（字体加载前后行高会变）。
(function () {{
  function fit() {{
    var card = document.querySelector('.bottom-card');
    var body = document.querySelector('.body');
    var tail = document.querySelector('.tail');
    if (!card || !body || !tail) return;
    var pad = Math.max(0, {CARD_GAP} + {TAIL_BOTTOM}
      + Math.ceil(card.getBoundingClientRect().height)
      - Math.ceil(tail.getBoundingClientRect().height));
    body.style.paddingBottom = pad + 'px';
  }}
  fit();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
}})();
</script>"""

    # 只把真正用到的字做字体子集（正文 + 固定小标题），否则 HTML 会有十几 MB
    plain = re.sub(r"<[^>]+>", "", body_html)
    css = build_css(mid_b64, plain, cfg.font(), cfg.palette)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{esc(_g(d, "title", default="战报"))}</title>
<style>{css}</style></head>
<body>
{body_html}
</body></html>
"""


def render(
    data: Any,
    out_path: str | Path,
    *,
    cfg: Config | None = None,
    scale: float | None = None,
    log: Callable[[str], None] | None = None,
) -> Path:
    """渲染成 PNG。返回实际落盘路径（目标被占用时会带时间戳另存）。"""
    cfg = cfg or Config()
    log = log or cfg.log
    scale = float(scale or cfg.render.scale)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "渲染需要 playwright：pip install 'sgs-zhanbao[render]' "
            "然后执行 python -m playwright install chromium"
        ) from exc

    d = _to_dict(data)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = build_html(d, cfg=cfg)
    html_path = out_path.with_suffix(".html")
    html_path.write_text(doc, encoding="utf-8")

    # 先截到临时文件再原子替换：直接覆盖已有 PNG 时，Windows 上偶发
    # OSError [Errno 22]（文件被索引/杀软/看图程序同时持有）
    tmp_path = out_path.with_name(f"{out_path.stem}.tmp-{os.getpid()}.png")
    moved = False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--force-color-profile=srgb"])
            try:
                page = browser.new_page(
                    viewport={"width": W, "height": cfg.render.viewport_height},
                    device_scale_factor=scale,
                )
                page.goto(html_path.resolve().as_uri(), wait_until="load")
                page.wait_for_timeout(cfg.render.settle_ms)
                _fit_bottom_card(page, art_dir(cfg) / "tail.png")
                _fix_seam(page, paper=cfg.palette.paper)
                page.screenshot(path=str(tmp_path), full_page=True)
            finally:
                browser.close()
        out = _atomic_replace(tmp_path, out_path, log=log)
        moved = True
        return out
    finally:
        # 只有成功落盘后才清理临时文件；失败时留着它，错误信息里会指向这个路径
        if moved and tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()


def _atomic_replace(tmp: Path, target: Path, *, log=print, retries: int = 24) -> Path:
    """把临时图落到目标路径。

    看图软件 / 杀软 / 索引服务可能正持有目标文件，Windows 上会 ``OSError``。
    先原地重试（约 12 秒），仍失败就换一个带时间戳的名字——
    绝不因为「名字撞车」把已经渲染好的图丢掉。
    """
    for attempt in range(retries):
        try:
            # 用 os.replace 而不是 Path.replace：语义相同，且测试可以打桩模拟被占用
            os.replace(tmp, target)  # noqa: PTH105
            return target
        except OSError:
            if attempt == retries - 1:
                break
            time.sleep(0.5)

    stamp = time.strftime("%H%M%S")
    candidates = [target.with_name(f"{target.stem}_{stamp}{target.suffix}")]
    candidates += [
        target.with_name(f"{target.stem}_{stamp}_{i}{target.suffix}") for i in range(1, 20)
    ]
    last_err: OSError | None = None
    for cand in candidates:
        try:
            os.replace(tmp, cand)  # noqa: PTH105
            log(f"⚠ 目标文件被占用，已另存：{cand}")
            return cand
        except OSError as exc:
            last_err = exc
            continue

    # 连改名都写不进去：保留临时文件，让用户还能拿到图，同时把原因说清楚
    raise RuntimeError(
        f"无法写入 {target}（{last_err}）。\n"
        f"  渲染结果已保存在临时文件：{tmp}\n"
        f"  请关闭正在预览该图片的程序后重试。"
    )


def render_report(
    data: ReportData | dict | str | Path,
    *,
    output_path: str | Path | None = None,
    cfg: Config | None = None,
    scale: float | None = None,
    log: Callable[[str], None] | None = None,
) -> Path:
    """对外主入口：``ReportData`` / dict / report.json 路径 → PNG。"""
    cfg = cfg or Config()
    if output_path is None:
        date = _g(_to_dict(data), "date", default="") or "draft"
        from .config import output_dir

        output_path = output_dir() / f"SGL战报_{date}.png"
    return render(data, output_path, cfg=cfg, scale=scale, log=log)


if __name__ == "__main__":  # 便于本地快速预览
    import sys

    argv = sys.argv[1:]
    src = argv[0] if argv else "data/report.json"
    dst = argv[1] if len(argv) > 1 else "output/preview.png"
    print(render_report(src, output_path=dst))
