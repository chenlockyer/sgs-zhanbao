"""配置、.env、路径与字体发现。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sgs_zhanbao import config
from sgs_zhanbao.config import (
    Config,
    LLMConfig,
    Palette,
    find_system_kaiti,
    home,
    load_api_key,
    load_dotenv,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """每个测试都从干净的环境变量开始，避免相互污染。"""
    for key in list(os.environ):
        if key.startswith("SGS_") or key == "DEEPSEEK_API_KEY":
            monkeypatch.delenv(key, raising=False)
    yield


class TestDotEnv:
    def test_basic(self, tmp_path: Path):
        p = tmp_path / ".env"
        p.write_text("DEEPSEEK_API_KEY=sk-abc\n", encoding="utf-8")
        for k in ("DEEPSEEK_API_KEY",):
            os.environ.pop(k, None)
        load_dotenv(p)
        assert os.environ["DEEPSEEK_API_KEY"] == "sk-abc"

    def test_quotes_stripped(self, tmp_path: Path):
        p = tmp_path / ".env"
        p.write_text('KEY_A="带引号"\nKEY_B=\'单引号\'\n', encoding="utf-8")
        load_dotenv(p)
        assert os.environ["KEY_A"] == "带引号"
        assert os.environ["KEY_B"] == "单引号"

    def test_comments_and_blanks(self, tmp_path: Path):
        p = tmp_path / ".env"
        p.write_text("# 注释\n\nKEY_C=值 # 行尾注释\n", encoding="utf-8")
        load_dotenv(p)
        assert os.environ["KEY_C"] == "值"

    def test_does_not_override_by_default(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("KEY_D", "真实值")
        p = tmp_path / ".env"
        p.write_text("KEY_D=文件值\n", encoding="utf-8")
        load_dotenv(p)
        assert os.environ["KEY_D"] == "真实值"

    def test_override_flag(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("KEY_E", "真实值")
        p = tmp_path / ".env"
        p.write_text("KEY_E=文件值\n", encoding="utf-8")
        load_dotenv(p, override=True)
        assert os.environ["KEY_E"] == "文件值"

    def test_missing_file_is_noop(self, tmp_path: Path):
        assert load_dotenv(tmp_path / "nope.env") == {}

    def test_returns_injected(self, tmp_path: Path):
        p = tmp_path / ".env"
        p.write_text("KEY_F=1\n", encoding="utf-8")
        assert load_dotenv(p) == {"KEY_F": "1"}


class TestHome:
    def test_default_is_cwd(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert home() == tmp_path.resolve()

    def test_dotenv_is_scoped_to_working_dir(self, tmp_path: Path, monkeypatch):
        """.env 从工作目录读，不是从仓库根读。

        这条很重要：CLI 会 ``load_dotenv()``，如果不跟着工作目录走，
        在别人机器上跑测试时会读到开发者仓库里的真实 .env（含密钥、
        SGS_ART 等），测试结果就依赖个人环境了。
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("SGS_LLM_MODEL=from-workdir\n", encoding="utf-8")
        load_dotenv()
        assert os.environ["SGS_LLM_MODEL"] == "from-workdir"

    def test_dotenv_not_read_from_elsewhere(self, tmp_path: Path, monkeypatch):
        other = tmp_path / "other"
        other.mkdir()
        (other / ".env").write_text("SGS_LLM_MODEL=should-not-load\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        load_dotenv()
        assert "SGS_LLM_MODEL" not in os.environ

    def test_env_override(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("SGS_HOME", str(tmp_path))
        assert home() == tmp_path.resolve()

    def test_subdirs(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("SGS_HOME", str(tmp_path))
        assert config.data_dir() == tmp_path / "data"
        assert config.output_dir() == tmp_path / "output"
        assert config.transcript_dir() == tmp_path / "voice2text"

    def test_ensure_dirs_creates(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("SGS_HOME", str(tmp_path / "work"))
        config.ensure_dirs()
        for d in ("data", "output", "voice2text"):
            assert (tmp_path / "work" / d).is_dir()


class TestLLMConfig:
    def test_defaults(self):
        c = LLMConfig()
        assert c.base_url == "https://api.deepseek.com"
        assert c.model == "deepseek-chat"
        assert c.max_retries >= 1

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SGS_LLM_MODEL", "deepseek-reasoner")
        monkeypatch.setenv("SGS_LLM_TEMPERATURE", "0.0")
        monkeypatch.setenv("SGS_LLM_MAX_TOKENS", "1024")
        c = LLMConfig()
        assert c.model == "deepseek-reasoner"
        assert c.temperature == 0.0
        assert c.max_tokens == 1024

    def test_bad_float_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("SGS_LLM_TEMPERATURE", "不是数字")
        assert LLMConfig().temperature == 0.35

    def test_with_overrides_ignores_none(self):
        c = LLMConfig()
        out = c.with_overrides(model="x", base_url=None)
        assert out.model == "x"
        assert out.base_url == c.base_url


class TestApiKey:
    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        assert load_api_key() == "sk-test"

    def test_missing_raises_with_hint(self):
        with pytest.raises(RuntimeError) as exc:
            load_api_key()
        assert ".env" in str(exc.value)
        assert "DEEPSEEK_API_KEY" in str(exc.value)

    def test_custom_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "sk-x")
        assert load_api_key("MY_KEY") == "sk-x"


class TestFontDiscovery:
    def test_finds_kaiti_on_windows(self):
        if os.name != "nt":
            pytest.skip("仅在 Windows 上验证")
        font = find_system_kaiti()
        assert font is not None
        assert font.is_file()

    def test_returns_none_when_nothing_found(self, monkeypatch):
        monkeypatch.setattr(config, "_FONT_CANDIDATES", ())
        assert find_system_kaiti() is None

    def test_config_font_prefers_explicit(self, tmp_path: Path):
        f = tmp_path / "my.ttf"
        f.write_bytes(b"\x00\x01\x00\x00")
        cfg = Config(font_path=f)
        assert cfg.font() == f

    def test_config_font_falls_back(self, tmp_path: Path):
        cfg = Config(font_path=tmp_path / "missing.ttf")
        cfg.log = lambda *_: None
        assert cfg.font() == find_system_kaiti()


class TestResourcePaths:
    def test_packaged_resources_exist(self):
        cfg = Config()
        assert cfg.glossary().is_file()
        assert (cfg.prompt_dir() / "proofread.md").is_file()
        assert (cfg.prompt_dir() / "verify_corrections.md").is_file()
        assert (cfg.prompt_dir() / "extract_report.md").is_file()
        for name in ("head.png", "mid.png", "tail.png"):
            assert (config.RES.art / name).is_file()

    def test_glossary_override(self, tmp_path: Path):
        p = tmp_path / "g.yaml"
        p.write_text("teams: {}\n", encoding="utf-8")
        assert Config(glossary_path=p).glossary() == p

    def test_prompts_override(self, tmp_path: Path):
        assert Config(prompt_path=tmp_path).prompt_dir() == tmp_path

    def test_env_prompts_override(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("SGS_PROMPTS", str(tmp_path))
        assert Config().prompt_dir() == tmp_path


class TestPalette:
    def test_every_field_reaches_css(self):
        """Palette 的每个字段都必须真的被渲染层读到。

        这条是防「Theme 式死配置」的回归测试：历史上 Theme 定义了 primary/
        accent/ink 等一堆字段，CSS 里一个都没用，改它静默无效。
        做法：把每个字段换成独一无二的颜色，检查它出现在 CSS 里。
        """
        from sgs_zhanbao.render import build_css

        probe = Palette(**{
            f: f"#{i:02X}{i * 3 % 256:02X}{200 - i:02X}"
            for i, f in enumerate(Palette.__dataclass_fields__)
        })
        css = build_css("AAA", "文字", None, probe)
        dead = [f for f, v in probe.__dict__.items() if v.lower() not in css.lower()]
        assert not dead, f"这些配色字段没有被渲染层使用：{dead}"

    def test_defaults_match_legacy_css(self):
        """默认色值必须与历史写死的 CSS 一致（换配置层不该改变出图）。"""
        p = Palette()
        assert p.headline == "#c2405f"
        assert p.deep == "#a83752"
        assert p.paper == "#FCEFEE"

    def test_env_override_single_field(self, monkeypatch):
        monkeypatch.setenv("SGS_COLOR_HEADLINE", "#3C78DC")
        p = Palette.from_env()
        assert p.headline == "#3C78DC"
        assert p.deep == Palette().deep, "只该覆盖被点到的那一个"

    def test_env_override_ignored_when_blank(self, monkeypatch):
        monkeypatch.setenv("SGS_COLOR_HEADLINE", "   ")
        assert Palette.from_env().headline == Palette().headline

    def test_config_uses_env_palette(self, monkeypatch):
        monkeypatch.setenv("SGS_COLOR_DEEP", "#2C6B52")
        assert Config().palette.deep == "#2C6B52"

    def test_palette_reaches_css(self):
        """这条是重点：历史上 Theme 定义了但 CSS 不读，改色静默无效。"""
        from sgs_zhanbao.render import build_css

        custom = Palette(headline="#00ff00", deep="#0000ff", paper="#ff0000")
        css = build_css("AAA", "文字", None, custom)
        assert "#00ff00" in css
        assert "#0000ff" in css
        assert "#ff0000" in css
        # 默认色不该再出现
        assert "#c2405f" not in css
        assert "#a83752" not in css
