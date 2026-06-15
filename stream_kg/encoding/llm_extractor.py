"""LLM-based 知识图谱抽取器（P3-X · Phase A）。

职责：
- 加载 `prompts/kg_extraction.txt` 模板
- 单 chunk：调 DeepSeek（含 max_retries 重试） → JSON 解析 → pydantic 校验
- 批量 chunk：asyncio.Semaphore 控制并发（默认 5，E5 决策）
- 永不抛异常给上游：失败用 `ChunkExtractionResult.extraction = None` + `attempt.error` 携带

参考：
- docs/spec/13-kg-llm-redesign.md §2（抽取流水线、错误处理矩阵）
- docs/planning/14-kg-llm-execution-plan.md §2（Phase A）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from stream_kg.config import settings
from stream_kg.kg.llm_models import KgExtraction
from stream_kg.llm import DeepSeekClient, DeepSeekError

logger = logging.getLogger(__name__)

# === 常量 ===
# prompt 文件位置（项目根 prompts/）
PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "kg_extraction.txt"
PROMPT_PLACEHOLDER = "{{CHUNK_TEXT}}"

# System 消息：与 PoC 一致；DeepSeek json_object 模式要求 prompt 含 "json" 字样，
# user prompt 里"严格 JSON 对象"已满足。
SYSTEM_MESSAGE = "You are a strict JSON extractor. Always return valid JSON only."

# DeepSeek 计价（CNY per million token；13 文档 §2.3，实际计费以官方为准）
_COST_PER_M_INPUT = 0.5
_COST_PER_M_OUTPUT = 1.5


def estimate_cost_yuan(prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens / 1_000_000) * _COST_PER_M_INPUT + (
        completion_tokens / 1_000_000
    ) * _COST_PER_M_OUTPUT


@dataclass(slots=True)
class ExtractionAttempt:
    """单 chunk 抽取的执行元数据，供 `kg_extraction_logs` 落库 + 监控统计。"""

    success: bool
    retries: int = 0  # 实际重试次数（0 = 一次成功）
    elapsed_sec: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_yuan: float = 0.0
    raw_output: str | None = None  # 最后一次 raw（成功 / 失败都保留供 debug）
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(slots=True)
class ChunkExtractionResult:
    """单 chunk 抽取的完整结果（数据 + 元数据）。"""

    chunk_id: str
    extraction: KgExtraction | None  # None = 抽取失败（attempt.error 有错因）
    attempt: ExtractionAttempt

    @property
    def success(self) -> bool:
        return self.extraction is not None and self.attempt.success


@dataclass(slots=True)
class BatchExtractionSummary:
    """批量抽取汇总统计。"""

    total_chunks: int
    ok_chunks: int
    failed_chunks: int
    elapsed_sec: float
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_yuan: float = 0.0
    results: list[ChunkExtractionResult] = field(default_factory=list)


class LlmExtractor:
    """LLM-based 单 chunk / 批量知识抽取。

    用法：
        ext = LlmExtractor()
        result = await ext.extract_chunk(chunk_id="c1", chunk_text="...")
        if result.success:
            for e in result.extraction.entities: ...

        summary = await ext.extract_batch([
            ("c1", "text 1"),
            ("c2", "text 2"),
        ])
        # summary.results 一一对应输入；summary.total_cost_yuan 等可写监控表
    """

    def __init__(
        self,
        *,
        client: DeepSeekClient | None = None,
        prompt_template: str | None = None,
        max_retries: int | None = None,
        max_tokens: int | None = None,
        timeout_sec: float | None = None,
        concurrency: int | None = None,
    ) -> None:
        self.client = client or DeepSeekClient()
        # 测试可注入 prompt 模板；生产从文件加载
        self.prompt_template = (
            prompt_template if prompt_template is not None else self._load_prompt()
        )
        if PROMPT_PLACEHOLDER not in self.prompt_template:
            raise ValueError(
                f"prompt template missing placeholder {PROMPT_PLACEHOLDER!r}; "
                f"check {PROMPT_PATH}"
            )
        self.max_retries = (
            max_retries if max_retries is not None else settings.kg_extraction_max_retries
        )
        self.max_tokens = (
            max_tokens if max_tokens is not None else settings.kg_extraction_max_tokens
        )
        self.timeout_sec = (
            timeout_sec if timeout_sec is not None else settings.kg_extraction_timeout_sec
        )
        self.concurrency = (
            concurrency if concurrency is not None else settings.kg_extraction_concurrency
        )

    @staticmethod
    def _load_prompt() -> str:
        if not PROMPT_PATH.exists():
            raise FileNotFoundError(
                f"prompt 文件不存在：{PROMPT_PATH}，请确认 prompts/kg_extraction.txt 已就位"
            )
        return PROMPT_PATH.read_text(encoding="utf-8")

    def _build_messages(self, chunk_text: str) -> list[dict[str, str]]:
        # 注意用 replace 而不是 .format()：prompt 含大量 `{` `}` JSON 字面量
        user_prompt = self.prompt_template.replace(PROMPT_PLACEHOLDER, chunk_text)
        return [
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": user_prompt},
        ]

    async def extract_chunk(
        self,
        *,
        chunk_id: str,
        chunk_text: str,
    ) -> ChunkExtractionResult:
        """对单 chunk 调 LLM，含重试，**永不抛异常**（结果用 ChunkExtractionResult 携带）。

        重试策略（13 文档 §2.5）：
        - HTTP / API error → retry up to `max_retries` 次
        - JSON 解析失败 → retry up to `max_retries` 次
        - pydantic schema 校验失败 → **不 retry**（schema 不对说明 prompt 出问题，
          重试只会拿到同样的错；返回 None 让上游进 failed 队列等待人工调 prompt）
        """
        messages = self._build_messages(chunk_text)
        attempt = ExtractionAttempt(success=False)

        t0 = time.perf_counter()
        last_raw: str | None = None
        last_usage: dict | None = None

        # 总尝试次数 = 首次 + max_retries 次重试
        for try_idx in range(self.max_retries + 1):
            try:
                resp = await self.client.chat_raw(
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=0.0,
                    response_format={"type": "json_object"},
                    timeout_sec=self.timeout_sec,
                )
            except DeepSeekError as e:
                attempt.error = f"LLM HTTP/API error (try {try_idx + 1}/{self.max_retries + 1}): {e}"
                attempt.retries = try_idx
                logger.warning(
                    "chunk %s extract HTTP retry %d/%d: %s",
                    chunk_id,
                    try_idx + 1,
                    self.max_retries + 1,
                    e,
                )
                continue

            # 拿 raw text + usage
            try:
                last_raw = resp["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as e:
                attempt.error = f"Unexpected response shape: {e}"
                attempt.retries = try_idx
                logger.warning("chunk %s bad response shape (try %d)", chunk_id, try_idx + 1)
                continue
            last_usage = resp.get("usage") or {}

            # JSON 解析
            try:
                payload = json.loads(last_raw)
            except json.JSONDecodeError as e:
                attempt.error = f"JSON decode fail: {e}; head={last_raw[:120]!r}"
                attempt.retries = try_idx
                logger.warning(
                    "chunk %s JSON decode fail (try %d): %s", chunk_id, try_idx + 1, e
                )
                continue

            # pydantic 校验（不 retry）
            try:
                extraction = KgExtraction.model_validate(payload)
            except ValidationError as e:
                attempt.error = f"Schema validation fail: {e.errors()[:3]}"
                attempt.retries = try_idx
                attempt.raw_output = last_raw
                attempt.prompt_tokens = (last_usage or {}).get("prompt_tokens", 0)
                attempt.completion_tokens = (last_usage or {}).get("completion_tokens", 0)
                attempt.cost_yuan = estimate_cost_yuan(
                    attempt.prompt_tokens, attempt.completion_tokens
                )
                attempt.elapsed_sec = time.perf_counter() - t0
                logger.error(
                    "chunk %s schema validation fail (no retry): %s",
                    chunk_id,
                    str(e.errors()[:2]),
                )
                return ChunkExtractionResult(
                    chunk_id=chunk_id, extraction=None, attempt=attempt
                )

            # 成功
            attempt.success = True
            attempt.retries = try_idx
            attempt.error = None
            attempt.raw_output = last_raw
            attempt.prompt_tokens = (last_usage or {}).get("prompt_tokens", 0)
            attempt.completion_tokens = (last_usage or {}).get("completion_tokens", 0)
            attempt.cost_yuan = estimate_cost_yuan(
                attempt.prompt_tokens, attempt.completion_tokens
            )
            attempt.elapsed_sec = time.perf_counter() - t0
            return ChunkExtractionResult(
                chunk_id=chunk_id, extraction=extraction, attempt=attempt
            )

        # 所有重试用尽
        attempt.elapsed_sec = time.perf_counter() - t0
        attempt.raw_output = last_raw
        attempt.prompt_tokens = (last_usage or {}).get("prompt_tokens", 0)
        attempt.completion_tokens = (last_usage or {}).get("completion_tokens", 0)
        attempt.cost_yuan = estimate_cost_yuan(
            attempt.prompt_tokens, attempt.completion_tokens
        )
        return ChunkExtractionResult(chunk_id=chunk_id, extraction=None, attempt=attempt)

    async def extract_batch(
        self,
        chunks: Sequence[tuple[str, str]],
        *,
        concurrency: int | None = None,
    ) -> BatchExtractionSummary:
        """批量并发抽取。

        参数：
            chunks: 列表，每项 (chunk_id, chunk_text)
            concurrency: 覆盖默认并发数

        返回：BatchExtractionSummary，含每个 chunk 的结果（保持输入顺序）+ 汇总统计。
        """
        sem = asyncio.Semaphore(concurrency or self.concurrency)

        async def _wrapped(chunk_id: str, chunk_text: str) -> ChunkExtractionResult:
            async with sem:
                return await self.extract_chunk(chunk_id=chunk_id, chunk_text=chunk_text)

        t0 = time.perf_counter()
        results = await asyncio.gather(*(_wrapped(cid, text) for cid, text in chunks))
        elapsed = time.perf_counter() - t0

        ok = sum(1 for r in results if r.success)
        total_pt = sum(r.attempt.prompt_tokens for r in results)
        total_ct = sum(r.attempt.completion_tokens for r in results)

        return BatchExtractionSummary(
            total_chunks=len(results),
            ok_chunks=ok,
            failed_chunks=len(results) - ok,
            elapsed_sec=elapsed,
            total_prompt_tokens=total_pt,
            total_completion_tokens=total_ct,
            total_cost_yuan=estimate_cost_yuan(total_pt, total_ct),
            results=list(results),
        )
