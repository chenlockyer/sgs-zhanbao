"""全局配置、路径解析与密钥读取。

设计目标
--------
别人 clone 下来 `pip install -e .` 就能跑，所以：

* **包内资源**（提示词、术语表、版式素材）跟着 wheel 走，用 ``importlib.resources``
  定位，安装成 zip / 装在 site-packages 里也能找到。
* **用户数据**（转写稿、结构化 JSON、成品图）永远落在用户自己的工作目录，
  绝不在 site-packages 里写文件。默认是当前目录，可用环境变量或 CLI 参数覆盖。

优先级（高 → 低）::

    显式参数 / 环境变量  >  .env 文件  >  内置默认值

环境变量
--------
``SGS_HOME``                    工作目录（默认当前目录）
``DEEPSEEK_API_KEY``            DeepSeek 密钥（必填）
``SGS_LLM_BASE_URL``            兼容 OpenAI 协议的其它网关
``SGS_LLM_MODEL``               模型名（默认 deepseek-chat）
``SGS_LLM_TEMPERATURE``         采样温度
``SGS_LLM_MAX_TOKENS``          单次输出上限
``SGS_LLM_TIMEOUT``             单次请求超时（秒）
``SGS_LLM_MAX_RETRIES``         失败重试次数
``SGS_LLM_JSON_MODE``           1/0，是否用 response_format=json_object
``SGS_GLOSSARY``                自定义术语表路径（覆盖包内那份）
``SGS_FONT``                    自定义正文字体路径（默认用系统楷体）
``SGS_REPORTER``                记者署名
``SGS_SCALE``                   出图倍率（默认 2.0）
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------- 包内资源

PACKAGE_NAME = "sgs_zhanbao"


class _Resources:
    """包内只读资源的定位器。

    优先用 ``importlib.resources``（支持 zip 安装），失败时退回 ``__file__`` 同目录。
    两种方式都返回真实 ``Path``，因为字体子集化需要读文件字节。
    """

    def __init__(self) -> None:
        self._root: Path | None = None

    @property
    def root(self) -> Path:
        if self._root is None:
            try:
                from importlib.resources import files

                self._root = Path(str(files(PACKAGE_NAME) / "data"))
            except Exception:  # noqa: BLE001 —— 极老环境 / 打包异常
                self._root = Path(__file__).resolve().parent / "data"
        return self._root

    @property
    def prompts(self) -> Path:
        return self.root / "prompts"

    @property
    def art(self) -> Path:
        return self.root / "art" / "v2"

    @property
    def glossary(self) -> Path:
        return self.root / "glossary.yaml"


RES = _Resources()


# ---------------------------------------------------------------- 工作目录

def home() -> Path:
    """用户工作目录：所有读写产物都落在这里。"""
    return Path(os.environ.get("SGS_HOME") or Path.cwd()).expanduser().resolve()


def data_dir() -> Path:
    return home() / "data"


def output_dir() -> Path:
    return home() / "output"


def transcript_dir() -> Path:
    return home() / "voice2text"


def ensure_dirs() -> None:
    for d in (data_dir(), output_dir(), transcript_dir()):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- .env

_ENV_PATTERN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> dict[str, str]:
    """读一个极简 .env（不引入 python-dotenv 依赖）。

    * 支持 ``KEY=value`` / ``KEY="value"`` / ``# 注释`` / 空行
    * ``override=False`` 时不覆盖已存在的真实环境变量
    * 返回本次真正注入的键值，便于 CLI 打印来源
    """
    p = Path(path) if path else home() / ".env"
    injected: dict[str, str] = {}
    if not p.is_file():
        return injected
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_PATTERN.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            # 去掉行尾注释：只有前面有空白才算注释
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if not value:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            injected[key] = value
    return injected


# ---------------------------------------------------------------- 主题 / 品牌

@dataclass
class Theme:
    """战报视觉主题。改这里就能换皮，不用碰渲染逻辑。"""

    name: str = "sgl-pink"
    primary: str = "#E8547C"        # 玫红（标题、强调）
    primary_dark: str = "#C43D63"
    accent: str = "#F5A3BE"         # 浅粉（分割线、描边）
    paper: str = "#FCEFEE"          # 纸面底色（也是渲染时的「缝隙检测色」）
    ink: str = "#2B2126"
    width: int = 928                # 设计稿逻辑宽度；出图倍率见 RenderConfig.scale


# ---------------------------------------------------------------- LLM

def _env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "y", "on"}


@dataclass
class LLMConfig:
    """兼容 OpenAI Chat Completions 协议的任何后端（默认 DeepSeek）。"""

    base_url: str = field(default_factory=lambda: _env_str("SGS_LLM_BASE_URL", "https://api.deepseek.com"))
    model: str = field(default_factory=lambda: _env_str("SGS_LLM_MODEL", "deepseek-chat"))
    api_key_env: str = "DEEPSEEK_API_KEY"
    temperature: float = field(default_factory=lambda: _env_float("SGS_LLM_TEMPERATURE", 0.35))
    max_tokens: int = field(default_factory=lambda: _env_int("SGS_LLM_MAX_TOKENS", 8192))
    timeout: int = field(default_factory=lambda: _env_int("SGS_LLM_TIMEOUT", 300))
    max_retries: int = field(default_factory=lambda: _env_int("SGS_LLM_MAX_RETRIES", 3))
    json_mode: bool = field(default_factory=lambda: _env_bool("SGS_LLM_JSON_MODE", True))

    def with_overrides(self, **kw) -> LLMConfig:
        return replace(self, **{k: v for k, v in kw.items() if v is not None})


# ---------------------------------------------------------------- 渲染

@dataclass
class RenderConfig:
    scale: float = field(default_factory=lambda: _env_float("SGS_SCALE", 2.0))  # 2x 出图，长图文字不糊
    viewport_height: int = 1200
    settle_ms: int = 700             # 等字体 / 图片解码
    reporter: str = field(default_factory=lambda: _env_str("SGS_REPORTER", "战报记者：王战答疑团-江南"))


# ---------------------------------------------------------------- 汇总

@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    theme: Theme = field(default_factory=Theme)
    render: RenderConfig = field(default_factory=RenderConfig)
    glossary_path: Path | None = None       # None = 用包内术语表
    prompt_path: Path | None = None         # None = 用包内提示词
    art_path: Path | None = None            # None = 用包内版式素材
    font_path: Path | None = None           # None = 用系统楷体
    log: Callable[[str], None] = field(default=lambda msg: print(msg))

    # ---------------------------------------------------------- 路径解析

    def prompt_dir(self) -> Path:
        """提示词目录：显式指定 > 环境变量 > 包内。"""
        for cand in (self.prompt_path, os.environ.get("SGS_PROMPTS")):
            if cand:
                p = Path(cand)
                if p.is_dir():
                    return p
        return RES.prompts

    def glossary(self) -> Path:
        if self.glossary_path:
            return Path(self.glossary_path)
        env = os.environ.get("SGS_GLOSSARY")
        return Path(env) if env else RES.glossary

    def font(self) -> Path | None:
        """正文字体：显式指定 > 环境变量 > 系统楷体 > None（退回浏览器默认）。"""
        for cand in (self.font_path, os.environ.get("SGS_FONT")):
            if cand:
                p = Path(cand)
                if p.is_file():
                    return p
                self.log(f"  ⚠ 指定字体不存在，改用系统楷体：{p}")
        return find_system_kaiti()

    def art_dir(self) -> Path:
        """版式三件套（head/mid/tail.png）所在目录。

        默认用包内那份。仓库里自带的切片是从三国杀官方宣传图衍生的，
        仅限个人 / 学习用途；商用请换成自己的素材，用 ``SGS_ART`` 指过去即可，
        不用重新打包（详见 NOTICE）。
        """
        if self.art_path:
            return Path(self.art_path)
        env = os.environ.get("SGS_ART")
        return Path(env) if env else RES.art


# ---------------------------------------------------------------- 字体发现

# 楷体优先级：Windows 自带 simkai.ttf，其次华文楷体，最后其它常见楷体
_FONT_CANDIDATES = (
    "simkai.ttf",       # Windows 楷体（覆盖完整 GBK）
    "STKAITI.TTF",      # 华文楷体（macOS / Office）
    "STKaiti.ttf",
    "KaiTi.ttf",
    "Kaiti SC.ttc",
    "AR PL UKai CN.ttf",
    "LXGWKaiTi.ttf",
    "LXGWWenKai-Regular.ttf",
)


def find_system_kaiti() -> Path | None:
    """在常见字体目录里找一款楷体，找不到返回 None。

    不把字体打进仓库：Windows 楷体、华文楷体的授权都不允许随代码分发，
    这里只在用户本机查找，仓库里只留说明（见 NOTICE 与 docs/版式与素材.md）。
    """
    roots: list[Path] = []
    win = os.environ.get("WINDIR")
    if win:
        roots.append(Path(win) / "Fonts")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    home_dir = Path.home()
    roots += [
        home_dir / "Library" / "Fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        home_dir / ".fonts",
        home_dir / ".local" / "share" / "fonts",
    ]

    for root in roots:
        if not root.is_dir():
            continue
        for name in _FONT_CANDIDATES:
            direct = root / name
            if direct.is_file():
                return direct
        # 递归一层（/usr/share/fonts 下面还有 truetype/ opentype/ 子目录）
        for name in _FONT_CANDIDATES:
            try:
                hit = next(root.rglob(name), None)
            except OSError:
                hit = None
            if hit is not None:
                return hit
    return None


# ---------------------------------------------------------------- 密钥

def load_api_key(env_var: str = "DEEPSEEK_API_KEY") -> str:
    """从环境变量取密钥（.env 会在 CLI 启动时先注入）。"""
    key = os.environ.get(env_var, "").strip()
    if key:
        return key
    raise RuntimeError(
        f"找不到 {env_var}。\n"
        f"  方式一：在项目根目录放一个 .env，写一行 {env_var}=sk-xxxx\n"
        f"  方式二：设置环境变量 {env_var}\n"
        f"  （模板见 .env.example）"
    )
