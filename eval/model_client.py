"""被测模型客户端：统一走 OpenAI 兼容 Chat Completions 接口。

被测模型（DeepSeek Flash、Qwen3.5-4B base / SFT）均通过 OpenAI 兼容服务接入。
本模块只负责取回复，并保留可追溯的采样参数、finish_reason 与 token usage。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from text_utils import strip_reasoning_trace


@dataclass(frozen=True)
class ChatResult:
    """一次模型调用的可见正文与可追溯元数据。"""

    content: str
    finish_reason: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    reasoning_chars: int = 0
    response_model: Optional[str] = None
    request: dict[str, Any] = field(default_factory=dict)

    def metadata(self) -> dict[str, Any]:
        return {
            "finish_reason": self.finish_reason,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_chars": self.reasoning_chars,
            "response_model": self.response_model,
            "request": dict(self.request),
        }


class ChatClient:
    def __init__(self, base_url: str, api_key: str = "none", model: str = "",
                 temperature: float = 0.2, max_tokens: int = 1024,
                 top_p: Optional[float] = None, seed: Optional[int] = None,
                 presence_penalty: Optional[float] = None,
                 frequency_penalty: Optional[float] = None,
                 stop: Optional[Any] = None,
                 extra_body: Optional[dict[str, Any]] = None,
                 max_model_len: Optional[int] = None,
                 model_revision: Optional[str] = None,
                 timeout: float = 120.0, max_retries: int = 4,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "none"
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.seed = seed
        self.presence_penalty = presence_penalty
        self.frequency_penalty = frequency_penalty
        self.stop = stop
        self.extra_body = dict(extra_body or {})
        self.max_model_len = max_model_len
        self.model_revision = model_revision
        self.timeout = timeout
        self.max_retries = max_retries
        self.transport = transport

    def request_parameters(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        for key in ("top_p", "seed", "presence_penalty", "frequency_penalty", "stop"):
            value = getattr(self, key)
            if value is not None:
                params[key] = value
        return params

    def metadata(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "model_revision": self.model_revision,
            "max_model_len": self.max_model_len,
            "request": self.request_parameters(),
            "extra_body_keys": sorted(self.extra_body),
        }

    def _build_payload(self, messages: list[dict[str, str]], json_mode: bool = False,
                       extra: Optional[dict] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        payload.update(self.request_parameters())
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.extra_body:
            payload.update(self.extra_body)
        if extra:
            payload.update(extra)
        return payload

    async def chat(self, messages: list[dict[str, str]], json_mode: bool = False,
                   extra: Optional[dict] = None) -> ChatResult:
        payload = self._build_payload(messages, json_mode=json_mode, extra=extra)
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout,
                                             transport=self.transport) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    choice = data["choices"][0]
                    message = choice.get("message") or {}
                    raw_content = message.get("content") or ""
                    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
                    visible = strip_reasoning_trace(raw_content)
                    reasoning_chars = len(reasoning) if reasoning else max(
                        0, len(raw_content) - len(visible))
                    usage = data.get("usage") or {}
                    return ChatResult(
                        content=visible,
                        finish_reason=choice.get("finish_reason"),
                        prompt_tokens=usage.get("prompt_tokens"),
                        completion_tokens=usage.get("completion_tokens"),
                        reasoning_chars=reasoning_chars,
                        response_model=data.get("model"),
                        request=dict(self.request_parameters()),
                    )
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError,
                    json.JSONDecodeError) as e:
                last_err = e
                await asyncio.sleep(min(30, 2 ** attempt) + random.random())
        raise RuntimeError(f"chat failed after {self.max_retries} retries: {last_err}")


class SystemPromptChatClient:
    """包装被测客户端：为每条请求注入部署态 system prompt（人格）。"""

    def __init__(self, inner: ChatClient, system_prompt: str):
        self.inner = inner
        self.system_prompt = system_prompt
        self.system_prompt_sha256 = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()

    def metadata(self) -> dict[str, Any]:
        meta = self.inner.metadata()
        meta["system_prompt_sha256"] = self.system_prompt_sha256
        meta["system_prompt_chars"] = len(self.system_prompt)
        return meta

    async def chat(self, messages: list[dict[str, str]], json_mode: bool = False,
                   extra: Optional[dict] = None) -> ChatResult:
        msgs = ([{"role": "system", "content": self.system_prompt}]
                if self.system_prompt else []) + messages
        result = await self.inner.chat(msgs, json_mode=json_mode, extra=extra)
        result.request["system_prompt_sha256"] = self.system_prompt_sha256
        return result
