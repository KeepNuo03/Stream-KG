"""DeepSeek API 客户端薄封装（OpenAI 兼容 path）。

职责：
- 屏蔽 httpx + endpoint 拼装 + auth header + payload schema 等通信细节
- 暴露统一 `chat()` / `chat_stream()` 接口给上层（RAG 生成 / KG 抽取等业务复用）
- 不耦合任何业务 system prompt（由调用方传入 messages）

设计取舍：
- 不实现 provider 抽象层（13 文档 §9 决策 1）。如果未来接 Qwen-Max / 本地模型，
  另开 `qwen_client.py` 并用 typing.Protocol 统一接口即可。
- 失败统一抛 `DeepSeekError`；上游决定 catch + 兜底，还是上抛让用户感知。
  这比直接返回兜底字符串好——KG 抽取必须知道失败，不能拿"LLM 调用失败"喂给图谱。
- chat_stream 不支持 response_format（OpenAI / DeepSeek 流式 + json_object 在很多
  provider 上有问题；KG 抽取也不需要流式，统一走 chat()）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from stream_kg.config import settings

logger = logging.getLogger(__name__)


class DeepSeekError(Exception):
    """DeepSeek API 调用失败的统一异常。"""


class DeepSeekClient:
    """DeepSeek API client（OpenAI 兼容 `POST {base}/chat/completions`）。

    使用：
        client = DeepSeekClient()  # 从 settings 取 key/base/model
        # 非流式
        text = await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )
        # 流式
        async for token in client.chat_stream(messages=...):
            print(token, end="")
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str | None = None,
        default_timeout_sec: float = 60.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.api_base = (api_base or settings.llm_api_base).rstrip("/")
        self.default_model = default_model or settings.llm_model
        self.default_timeout_sec = default_timeout_sec

    @property
    def is_ready(self) -> bool:
        return bool(self.api_key)

    @property
    def endpoint(self) -> str:
        # 现有 rag_generator 一直走 `{base}/chat/completions`（base 不带 /v1），
        # 实测 DeepSeek 对 `/chat/completions` 与 `/v1/chat/completions` 都返回 200。
        # 为兼容存量 .env 配置，这里继承同一路径。
        return f"{self.api_base}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        max_tokens: int | None,
        temperature: float | None,
        response_format: dict[str, Any] | None,
        thinking_disabled: bool,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else settings.llm_max_tokens,
            "temperature": temperature if temperature is not None else settings.llm_temperature,
            "stream": stream,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if thinking_disabled:
            # DeepSeek V4 默认可能先输出 reasoning_content；关闭后可更快出最终 token，
            # 并避免 KG 抽取拿到一长串思考过程混在 JSON 之前。
            payload["thinking"] = {"type": "disabled"}
        return payload

    async def chat_raw(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        thinking_disabled: bool = True,
        timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        """非流式调用，返回 DeepSeek 完整响应 dict（含 choices / usage）。

        失败时抛 `DeepSeekError`。
        给需要 `usage` 字段做成本统计的调用方（如 KG 抽取）使用；
        只需要文本的调用方（如 RAG）请用 `chat()`。
        """
        if not self.is_ready:
            raise DeepSeekError("DeepSeek client 未配置 api_key")

        payload = self._build_payload(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
            thinking_disabled=thinking_disabled,
            stream=False,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout_sec or self.default_timeout_sec) as client:
                response = await client.post(self.endpoint, headers=self._headers(), json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response is not None else ""
            status = e.response.status_code if e.response is not None else "?"
            raise DeepSeekError(f"HTTP {status}: {body}") from e
        except httpx.HTTPError as e:
            raise DeepSeekError(f"HTTP error: {e!r}") from e

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        thinking_disabled: bool = True,
        timeout_sec: float | None = None,
    ) -> str:
        """非流式调用，返回 `choices[0].message.content` 字符串。

        失败时抛 `DeepSeekError`；上游决定 catch 后兜底或上抛。
        """
        data = await self.chat_raw(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
            thinking_disabled=thinking_disabled,
            timeout_sec=timeout_sec,
        )
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise DeepSeekError(f"Unexpected response shape: {str(data)[:300]}") from e

    async def chat_stream(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        thinking_disabled: bool = True,
    ) -> AsyncIterator[str]:
        """流式调用，迭代输出 token 片段。

        失败时在迭代器内抛 `DeepSeekError`；上游可在 `async for` 外层 try 处理。
        """
        if not self.is_ready:
            raise DeepSeekError("DeepSeek client 未配置 api_key")

        payload = self._build_payload(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=None,
            thinking_disabled=thinking_disabled,
            stream=True,
        )

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    self.endpoint,
                    headers=self._headers(),
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            parsed = json.loads(data)
                        except json.JSONDecodeError:
                            logger.warning("DeepSeek stream: bad JSON line: %r", data[:120])
                            continue
                        token = (
                            parsed.get("choices", [{}])[0].get("delta", {}).get("content")
                        )
                        if isinstance(token, str) and token:
                            yield token
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response is not None else ""
            status = e.response.status_code if e.response is not None else "?"
            raise DeepSeekError(f"HTTP {status}: {body}") from e
        except httpx.HTTPError as e:
            raise DeepSeekError(f"HTTP error: {e!r}") from e
