"""Unit tests for LlmExtractor (P3-X · Phase A)。

Mock 策略：用 unittest.mock.AsyncMock 替换 DeepSeekClient.chat_raw，
完全不打真实 HTTP，避免烧 token + 不确定结果。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from stream_kg.encoding.llm_extractor import (
    LlmExtractor,
    estimate_cost_yuan,
)
from stream_kg.llm import DeepSeekError


def _mock_response(
    content: str, *, prompt_tokens: int = 100, completion_tokens: int = 200
) -> dict:
    """构造 DeepSeek chat_raw() 风格的返回值。"""
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def _valid_json_str(n_entities: int = 2) -> str:
    payload = {
        "entities": [
            {
                "name": f"E{i}",
                "type": "method",
                "salience": 0.8,
                "aliases": [],
                "description": "",
            }
            for i in range(n_entities)
        ],
        "relations": [],
    }
    return json.dumps(payload)


def _make_extractor(
    *,
    max_retries: int = 2,
    prompt_template: str | None = None,
) -> tuple[LlmExtractor, AsyncMock]:
    """构造一个 mock 了 chat_raw 的 LlmExtractor。"""
    fake_client = AsyncMock()
    extractor = LlmExtractor(
        client=fake_client,
        prompt_template=prompt_template or "PROMPT: {{CHUNK_TEXT}}",
        max_retries=max_retries,
        max_tokens=2048,
        timeout_sec=5.0,
        concurrency=2,
    )
    return extractor, fake_client


# ---------- 配置 / 初始化 ----------


def test_extractor_rejects_prompt_without_placeholder() -> None:
    with pytest.raises(ValueError, match="placeholder"):
        LlmExtractor(
            client=AsyncMock(),
            prompt_template="no placeholder here",
            max_retries=1,
        )


def test_estimate_cost_yuan_basic() -> None:
    # 1M input + 1M output = 0.5 + 1.5 = 2.0 CNY
    assert estimate_cost_yuan(1_000_000, 1_000_000) == pytest.approx(2.0)
    assert estimate_cost_yuan(0, 0) == 0.0


# ---------- 正常通路 ----------


async def test_extract_chunk_success_first_try() -> None:
    extractor, fake = _make_extractor()
    fake.chat_raw.return_value = _mock_response(
        _valid_json_str(3), prompt_tokens=120, completion_tokens=300
    )

    result = await extractor.extract_chunk(chunk_id="c1", chunk_text="hello")

    assert result.success is True
    assert result.extraction is not None
    assert len(result.extraction.entities) == 3
    assert result.attempt.retries == 0
    assert result.attempt.prompt_tokens == 120
    assert result.attempt.completion_tokens == 300
    assert result.attempt.cost_yuan == pytest.approx(estimate_cost_yuan(120, 300))
    assert result.attempt.error is None
    assert result.attempt.raw_output is not None
    fake.chat_raw.assert_awaited_once()


async def test_extract_chunk_passes_chunk_into_prompt() -> None:
    extractor, fake = _make_extractor(prompt_template="HEAD\n{{CHUNK_TEXT}}\nTAIL")
    fake.chat_raw.return_value = _mock_response(_valid_json_str(1))

    await extractor.extract_chunk(chunk_id="c1", chunk_text="MY_TEXT")

    call_kwargs = fake.chat_raw.await_args.kwargs
    user_msg = call_kwargs["messages"][1]["content"]
    assert "MY_TEXT" in user_msg
    assert "HEAD" in user_msg and "TAIL" in user_msg
    # 关键参数透传
    assert call_kwargs["temperature"] == 0.0
    assert call_kwargs["response_format"] == {"type": "json_object"}


# ---------- HTTP 错误重试 ----------


async def test_extract_chunk_retries_on_http_error_then_succeeds() -> None:
    extractor, fake = _make_extractor(max_retries=2)
    fake.chat_raw.side_effect = [
        DeepSeekError("HTTP 500: server busy"),
        _mock_response(_valid_json_str(2)),
    ]

    result = await extractor.extract_chunk(chunk_id="c1", chunk_text="hello")

    assert result.success is True
    assert result.attempt.retries == 1
    assert fake.chat_raw.await_count == 2


async def test_extract_chunk_exhausts_retries_returns_failure() -> None:
    extractor, fake = _make_extractor(max_retries=2)
    fake.chat_raw.side_effect = DeepSeekError("HTTP 503: persistent")

    result = await extractor.extract_chunk(chunk_id="c1", chunk_text="hi")

    assert result.success is False
    assert result.extraction is None
    assert "HTTP 503" in (result.attempt.error or "")
    # max_retries=2 → 共 1 首次 + 2 重试 = 3 次
    assert fake.chat_raw.await_count == 3


# ---------- JSON 解析重试 ----------


async def test_extract_chunk_retries_on_bad_json_then_succeeds() -> None:
    extractor, fake = _make_extractor(max_retries=2)
    fake.chat_raw.side_effect = [
        _mock_response("this is not json {"),
        _mock_response(_valid_json_str(1)),
    ]

    result = await extractor.extract_chunk(chunk_id="c1", chunk_text="hi")

    assert result.success is True
    assert fake.chat_raw.await_count == 2


# ---------- Schema 校验失败：不重试 ----------


async def test_extract_chunk_schema_failure_does_not_retry() -> None:
    extractor, fake = _make_extractor(max_retries=2)
    # 合法 JSON 但 schema 错（type 不在 12 种枚举内）
    bad_payload = json.dumps(
        {
            "entities": [{"name": "X", "type": "INVALID_TYPE", "salience": 0.8}],
            "relations": [],
        }
    )
    fake.chat_raw.return_value = _mock_response(bad_payload)

    result = await extractor.extract_chunk(chunk_id="c1", chunk_text="hi")

    assert result.success is False
    assert result.extraction is None
    assert "validation" in (result.attempt.error or "").lower()
    # schema 错 → 不 retry，只调用 1 次
    assert fake.chat_raw.await_count == 1
    # raw_output 仍保存，便于人工 debug prompt
    assert result.attempt.raw_output is not None


# ---------- 治理（端到端：raw → 治理后） ----------


async def test_extract_chunk_truncates_when_llm_returns_too_many_entities() -> None:
    extractor, fake = _make_extractor()
    # LLM 返回 20 个 → 应被治理截到 12 个
    big_payload = json.dumps(
        {
            "entities": [
                {"name": f"E{i}", "type": "method", "salience": 0.5 + i * 0.02}
                for i in range(20)
            ],
            "relations": [],
        }
    )
    fake.chat_raw.return_value = _mock_response(big_payload)

    result = await extractor.extract_chunk(chunk_id="c1", chunk_text="hi")

    assert result.success is True
    assert result.extraction is not None
    assert len(result.extraction.entities) == 12


# ---------- batch 并发 ----------


async def test_extract_batch_preserves_order_and_aggregates_stats() -> None:
    extractor, fake = _make_extractor()
    fake.chat_raw.return_value = _mock_response(
        _valid_json_str(2), prompt_tokens=100, completion_tokens=200
    )

    chunks = [(f"c{i}", f"text {i}") for i in range(4)]
    summary = await extractor.extract_batch(chunks, concurrency=2)

    assert summary.total_chunks == 4
    assert summary.ok_chunks == 4
    assert summary.failed_chunks == 0
    assert summary.total_prompt_tokens == 400
    assert summary.total_completion_tokens == 800
    assert summary.total_cost_yuan == pytest.approx(estimate_cost_yuan(400, 800))
    # 顺序保持（asyncio.gather 保序）
    assert [r.chunk_id for r in summary.results] == ["c0", "c1", "c2", "c3"]


async def test_extract_batch_handles_partial_failure() -> None:
    extractor, fake = _make_extractor(max_retries=0)

    counter = {"n": 0}

    async def mock_chat_raw(**_kwargs: object) -> dict:
        counter["n"] += 1
        # 第 2 个失败
        if counter["n"] == 2:
            raise DeepSeekError("HTTP 500 (mock)")
        return _mock_response(_valid_json_str(1))

    fake.chat_raw.side_effect = mock_chat_raw

    # concurrency=1 让 sem(1) 保证顺序，便于断言哪个失败
    summary = await extractor.extract_batch(
        [("c1", "a"), ("c2", "b"), ("c3", "c")], concurrency=1
    )

    assert summary.total_chunks == 3
    assert summary.ok_chunks == 2
    assert summary.failed_chunks == 1
    # c2 失败
    by_id = {r.chunk_id: r for r in summary.results}
    assert by_id["c1"].success is True
    assert by_id["c2"].success is False
    assert by_id["c3"].success is True
    assert "HTTP 500" in (by_id["c2"].attempt.error or "")
