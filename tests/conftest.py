"""pytest 共享夹具。

原则：**测试不碰网络、不花钱、不需要 API key**。所有 LLM 调用都打桩，
渲染测试只在 playwright 可用时才跑（否则 skip，不 fail）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import sgs_zhanbao  # noqa: E402

# ---------------------------------------------------------------- 转写稿样例

TENCENT_SAMPLE = """解说甲 共享音频(00:00:05): 欢迎来到SGL主播赛循环赛第五天的比赛。
解说甲 共享音频(00:00:12): 今天是开机大队对阵败者食尘。
解说甲 共享音频(00:01:30): 刘焉这边直接反叫了四分，很有信心。
解说甲 共享音频(00:02:10): 摸到丈八蛇矛，杀穿了对面的诸葛亮。
解说甲 共享音频(00:03:00): 这一局开机大队拿下了，比分十四比十。
"""

BRACKET_SAMPLE = """[00:00:05] 欢迎来到SGL主播赛循环赛第五天的比赛。
[00:01:30] 刘焉这边直接反叫了四分，很有信心。
[00:02:10] 摸到丈八蛇矛，杀穿了对面的诸葛亮。
"""

PLAIN_SAMPLE = """欢迎来到今天的比赛。
刘焉反叫四分，摸到丈八蛇矛。
"""


@pytest.fixture
def tencent_txt(tmp_path: Path) -> Path:
    p = tmp_path / "20261105192559-转写_测试会议-逐字稿文本-1.txt"
    p.write_text(TENCENT_SAMPLE, encoding="utf-8")
    return p


@pytest.fixture
def bracket_txt(tmp_path: Path) -> Path:
    p = tmp_path / "20261106-流式转写.txt"
    p.write_text(BRACKET_SAMPLE, encoding="utf-8")
    return p


@pytest.fixture
def plain_txt(tmp_path: Path) -> Path:
    p = tmp_path / "plain.txt"
    p.write_text(PLAIN_SAMPLE, encoding="utf-8")
    return p


@pytest.fixture
def report_dict() -> dict:
    return {
        "title": "赛事速递 | 11.5 SGL主播赛战报",
        "date": "2026-11-05",
        "matches": [
            {
                "index": 1,
                "team_a": "开机大队",
                "score_a": 14,
                "score_b": 10,
                "team_b": "败者食尘",
                "note": "",
            }
        ],
        "spotlights": [
            {"title": "刘焉反叫4分", "body": "反叫四分后靠丈八蛇矛连杀两人。"},
            {"title": "紫金冠不砸", "body": "农民桃管够，开机大队拿下首场胜利。"},
        ],
        "moments": [{"title": "丈八蛇矛", "body": "摸到丈八启动，直接拿下。"}],
        "bidding": {
            "team_a": "开机大队：3分起步的激进策略",
            "team_b": "败者食尘：多当农民少叫地主",
            "key_round": "刘焉反叫4分成胜负手",
        },
        "highlights": [
            {"label": "最强地主", "detail": "魔孙权"},
            {"label": "最强农民", "detail": "张让鲍信"},
        ],
        "tomorrow": "明晚7点、9点直播",
        "full_text": "",
        "meta": {"event": "SGL主播赛循环赛"},
    }


@pytest.fixture
def report_json(tmp_path: Path, report_dict: dict) -> Path:
    p = tmp_path / "report.json"
    p.write_text(json.dumps(report_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------- LLM 打桩

class FakeLLM:
    """按顺序吐出预设响应，并模拟 chat_json 的 JSON 解析。

    用法::

        client = FakeLLM(['{"corrections": []}', '{"verdicts": []}'])
    """

    def __init__(self, responses: list[str | dict], *, fail_times: int = 0) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict]] = []
        self.fail_times = fail_times
        from sgs_zhanbao.llm import Usage

        self.usage = Usage()

    def _next(self) -> str:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("模拟网络失败")
        if not self.responses:
            raise AssertionError("FakeLLM 的预设响应已用尽")
        item = self.responses.pop(0)
        # str 视为「模型原话」原样返回；dict/list 序列化成 JSON 文本
        return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)

    def chat(self, messages, **kw) -> str:  # noqa: ARG002
        self.calls.append(messages)
        self.usage.add({"prompt_tokens": 10, "completion_tokens": 5})
        return self._next()

    def chat_json(self, messages, **kw):  # noqa: ARG002
        from sgs_zhanbao.llm import extract_json

        self.calls.append(messages)
        self.usage.add({"prompt_tokens": 10, "completion_tokens": 5})
        return extract_json(self._next())


@pytest.fixture
def fake_llm():
    return FakeLLM


# ---------------------------------------------------------------- 渲染可用性

_BROWSER_OK = None   # 三态：None=还没测过，True/False=结论（不在模块级写 X|Y 注解，3.9 会炸）


def browser_launchable() -> bool:
    """真开一次浏览器（结果缓存，避免每个测试都启动一次）。

    光检查 executable_path 不够：playwright 实际启动的可能是
    headless shell（另一个二进制），路径存在但它缺失照样跑不起来。
    """
    global _BROWSER_OK
    if _BROWSER_OK is None:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                if not Path(p.chromium.executable_path).is_file():
                    _BROWSER_OK = False
                else:
                    browser = p.chromium.launch(args=["--no-sandbox"])
                    browser.close()
                    _BROWSER_OK = True
        except Exception:  # noqa: BLE001
            _BROWSER_OK = False
    return _BROWSER_OK


requires_browser = pytest.mark.skipif(
    not browser_launchable(),
    reason="需要可启动的 chromium（pip install '.[render]' 后 playwright install chromium）",
)


@pytest.fixture(scope="session")
def pkg_root() -> Path:
    return Path(sgs_zhanbao.__file__).resolve().parent
