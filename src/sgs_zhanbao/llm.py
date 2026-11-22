"""LLM 调用封装：重试、JSON 强制解析、用量统计。

只依赖标准库（urllib），不引入 openai SDK —— 少一个依赖，也不用担心 SDK 版本
和网关兼容性。协议按 OpenAI Chat Completions：
``POST {base_url}/chat/completions``。
"""

from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import Config, load_api_key


class LLMError(RuntimeError):
    """LLM 调用失败（网络、鉴权、限流、返回不可解析都归到这里）。"""


@dataclass
class Usage:
    """累计 token 用量，跑完一次流水线打印出来。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    def add(self, u: dict | None) -> None:
        u = u or {}
        self.prompt_tokens += int(u.get("prompt_tokens") or 0)
        self.completion_tokens += int(u.get("completion_tokens") or 0)
        self.calls += 1

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
        }


# ---------------------------------------------------------------- JSON 兜底解析

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.S)


def _repair_json(text: str) -> str:
    """修几个模型高频犯的小毛病：尾逗号、全角引号、单引号键。"""
    s = text
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\uff1a", ":").replace("\uff0c", ",")
    s = re.sub(r",\s*([}\]])", r"\1", s)          # 尾逗号
    s = re.sub(r"//[^\n]*", "", s)                # 行注释
    return s


def extract_json(text: str) -> Any:
    """从模型输出里稳健地抠出 JSON。

    逐级降级：直接解析 → 去 markdown 围栏 → 截取首尾括号 → 修常见毛病后重试。
    深度思考模型有时会先把推理写出来再给 JSON，所以不能只试一次 ``json.loads``。
    """
    if not isinstance(text, str) or not text.strip():
        raise LLMError("模型返回空内容，无法解析 JSON")

    raw = text.strip()
    candidates: list[str] = [raw]

    if m := _FENCE_RE.search(raw):
        candidates.insert(0, m.group(1).strip())

    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = raw.find(open_ch), raw.rfind(close_ch)
        if start != -1 and end > start:
            candidates.append(raw[start : end + 1])

    seen: set[str] = set()
    for cand in candidates:
        for variant in (cand, _repair_json(cand)):
            if variant in seen:
                continue
            seen.add(variant)
            try:
                return json.loads(variant)
            except json.JSONDecodeError:
                continue

    raise LLMError(
        "无法从模型输出中解析 JSON。前 500 字符：\n" + raw[:500]
    )


# ---------------------------------------------------------------- 客户端

class LLMClient:
    """最小可用的 Chat 客户端，带指数退避重试。"""

    def __init__(
        self,
        cfg: Config | None = None,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.cfg = cfg or Config()
        self.api_key = api_key or load_api_key(self.cfg.llm.api_key_env)
        self.base_url = (base_url or self.cfg.llm.base_url).rstrip("/")
        self.model = model or self.cfg.llm.model
        self.usage = Usage()

    # ---------------------------------------------------------- 公开接口

    def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        c = self.cfg.llm
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": c.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or c.max_tokens,
        }
        payload["stream"] = False
        if json_mode and c.json_mode:
            payload["response_format"] = {"type": "json_object"}

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"{self.base_url}/chat/completions"
        last_err: Exception | None = None

        for attempt in range(1, c.max_retries + 1):
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=c.timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8", errors="replace"))
                self.usage.add(body.get("usage"))
                return self._content_of(body)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="ignore")[:400]
                last_err = LLMError(f"HTTP {e.code}：{detail}")
                # 4xx 里只有 408/409/429 值得重试，其余（401 密钥错、400 参数错）重试无意义
                if 400 <= e.code < 500 and e.code not in {408, 409, 429}:
                    raise last_err from e
            except urllib.error.URLError as e:
                last_err = LLMError(f"网络错误：{e.reason}")
            except TimeoutError:
                last_err = LLMError(f"请求超时（{c.timeout}s）")
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
                last_err = LLMError(f"响应结构异常：{e}")

            if attempt < c.max_retries:
                # 指数退避 + 抖动，避免多进程同时重试打爆网关
                time.sleep(min(2 ** attempt, 20) + random.uniform(0, 1))

        raise LLMError(f"LLM 调用失败（已重试 {c.max_retries} 次）：{last_err}")

    def chat_json(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """要求模型返回 JSON 对象并解析。

        解析失败会带着「你的上一次输出不是合法 JSON」再要一次，最多两轮；
        有些网关不支持 ``response_format``，这条兜底路径能让它们也能用。
        """
        text = self.chat(
            messages, json_mode=True, temperature=temperature, max_tokens=max_tokens
        )
        try:
            return extract_json(text)
        except LLMError:
            retry_messages = list(messages) + [
                {"role": "assistant", "content": text[:2000]},
                {
                    "role": "user",
                    "content": (
                        "你上面的输出不是合法 JSON，无法被 json.loads 解析。"
                        "请只重新输出一个合法的 JSON 对象，不要任何解释、不要 markdown 围栏。"
                    ),
                },
            ]
            text2 = self.chat(
                retry_messages, json_mode=True, temperature=0.0, max_tokens=max_tokens
            )
            return extract_json(text2)

    # ---------------------------------------------------------- 内部

    @staticmethod
    def _content_of(body: dict) -> str:
        choices = body.get("choices") or []
        if not choices:
            raise LLMError(f"响应里没有 choices：{str(body)[:300]}")
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            # 少数网关返回分段数组
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise LLMError(f"响应 content 为空：{str(body)[:300]}")
        return content
