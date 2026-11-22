"""LLM 封装的 JSON 兜底解析与重试。"""

from __future__ import annotations

import json

import pytest

from sgs_zhanbao.llm import LLMClient, LLMError, Usage, extract_json


class TestExtractJson:
    def test_plain(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_array(self):
        assert extract_json('[1, 2]') == [1, 2]

    def test_markdown_fence(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_fence_without_language(self):
        assert extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_leading_prose(self):
        text = '好的，这是结果：\n{"corrections": []}\n希望有帮助。'
        assert extract_json(text) == {"corrections": []}

    def test_trailing_comma(self):
        assert extract_json('{"a": 1, "b": [1, 2,],}') == {"a": 1, "b": [1, 2]}

    def test_fullwidth_quotes(self):
        assert extract_json('{\u201ca\u201d: 1}') == {"a": 1}

    def test_line_comment(self):
        assert extract_json('{"a": 1 // 备注\n}') == {"a": 1}

    def test_nested_prose_between_brackets(self):
        text = '思考过程 {"a": {"b": [1, 2]}} 结束语'
        assert extract_json(text) == {"a": {"b": [1, 2]}}

    def test_garbage_raises(self):
        with pytest.raises(LLMError, match="无法从模型输出中解析 JSON"):
            extract_json("完全不是 JSON")

    def test_empty_raises(self):
        with pytest.raises(LLMError, match="空内容"):
            extract_json("   ")


class TestUsage:
    def test_accumulates(self):
        u = Usage()
        u.add({"prompt_tokens": 10, "completion_tokens": 5})
        u.add({"prompt_tokens": 1, "completion_tokens": 2})
        assert u.as_dict() == {
            "calls": 2,
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        }

    def test_missing_usage(self):
        u = Usage()
        u.add(None)
        assert u.calls == 1
        assert u.prompt_tokens == 0


class TestChatJsonRepair:
    """第一次吐出坏 JSON 时，应自动再要一次，而不是直接失败。"""

    def _client(self, monkeypatch, responses):
        from sgs_zhanbao.config import Config

        client = LLMClient(Config(), api_key="sk-test")
        it = iter(responses)
        monkeypatch.setattr(client, "chat", lambda *a, **kw: next(it))
        return client

    def test_retries_once_on_bad_json(self, monkeypatch):
        client = self._client(monkeypatch, ["这是坏的", '{"ok": true}'])
        assert client.chat_json([{"role": "user", "content": "x"}]) == {"ok": True}

    def test_gives_up_after_second_bad_json(self, monkeypatch):
        client = self._client(monkeypatch, ["坏的", "还是坏的"])
        with pytest.raises(LLMError):
            client.chat_json([{"role": "user", "content": "x"}])


class TestContentExtraction:
    def test_list_content_is_joined(self):
        body = {"choices": [{"message": {"content": [{"text": "a"}, {"text": "b"}]}}]}
        assert LLMClient._content_of(body) == "ab"

    def test_empty_choices_raises(self):
        with pytest.raises(LLMError, match="没有 choices"):
            LLMClient._content_of({"choices": []})

    def test_empty_content_raises(self):
        body = {"choices": [{"message": {"content": "   "}}]}
        with pytest.raises(LLMError, match="content 为空"):
            LLMClient._content_of(body)


class TestChatHttpErrors:
    def test_401_not_retried(self, monkeypatch):
        import urllib.error

        from sgs_zhanbao.config import Config

        client = LLMClient(Config(), api_key="sk-test")
        calls = {"n": 0}

        def boom(req, timeout):  # noqa: ARG001
            calls["n"] += 1
            raise urllib.error.HTTPError("u", 401, "unauthorized", {}, None)

        monkeypatch.setattr("urllib.request.urlopen", boom)
        with pytest.raises(LLMError, match="HTTP 401"):
            client.chat([{"role": "user", "content": "x"}])
        assert calls["n"] == 1, "4xx（非 429）不该重试"

    def test_retries_then_succeeds(self, monkeypatch):
        import urllib.error

        from sgs_zhanbao.config import Config, LLMConfig

        cfg = Config(llm=LLMConfig(max_retries=3))
        client = LLMClient(cfg, api_key="sk-test")
        state = {"n": 0}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": "ok"}}], "usage": {}}
                ).encode()

        def flaky(req, timeout):  # noqa: ARG001
            state["n"] += 1
            if state["n"] < 3:
                raise urllib.error.URLError("temporary")
            return FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", flaky)
        monkeypatch.setattr("time.sleep", lambda *_: None)
        assert client.chat([{"role": "user", "content": "x"}]) == "ok"
        assert state["n"] == 3
