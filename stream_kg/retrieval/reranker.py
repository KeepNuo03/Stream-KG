"""Reranker 客户端：调用本地 reranker server，二阶段排序检索 chunk。

设计与 Embedder 同形：
- 仅在 ConnectError/ConnectTimeout 才触发熔断窗口，HTTP 错误本次降级、不锁后续；
- max_pairs_per_call 控制每次请求的 pair 数量，避免一次请求超时；
- 失败时不抛出，而是返回原向量排序（让上层 fallback 到 vector-only）。
"""

from __future__ import annotations

import time

import httpx

from stream_kg.kg.models import RetrievalChunk


class RerankerClient:
    """HTTP 客户端：把一组 (query, chunk) 送给本地 reranker 服务并取回新排序。"""

    def __init__(
        self,
        *,
        server_url: str,
        enabled: bool,
        timeout_sec: float = 90.0,
        connect_timeout_sec: float = 2.0,
        fallback_cooldown_sec: int = 5,
        max_pairs_per_call: int = 8,
        instruction: str = "",
        max_document_chars: int = 800,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.enabled = enabled
        self.timeout_sec = timeout_sec
        self.connect_timeout_sec = connect_timeout_sec
        self.fallback_cooldown_sec = fallback_cooldown_sec
        self.max_pairs_per_call = max(max_pairs_per_call, 1)
        self.instruction = instruction
        self.max_document_chars = max(max_document_chars, 200)
        self._server_unavailable_until = 0.0
        self._last_backend_mode: str = "off" if not enabled else "unknown"

    @property
    def last_backend_mode(self) -> str:
        return self._last_backend_mode

    async def rerank(
        self,
        *,
        query: str,
        chunks: list[RetrievalChunk],
        top_k: int | None = None,
    ) -> list[RetrievalChunk]:
        """按 reranker 分数重排候选 chunk；失败则原样返回。"""
        if not self.enabled or not chunks:
            self._last_backend_mode = "off" if not self.enabled else "empty"
            return chunks
        if time.monotonic() < self._server_unavailable_until:
            self._last_backend_mode = "cooldown"
            return chunks

        pairs: list[dict[str, str]] = []
        for chunk in chunks:
            text = (chunk.chunk.content or "")[: self.max_document_chars]
            pairs.append({"query": query, "document": text})

        scores: list[float] = []
        try:
            timeout = httpx.Timeout(self.timeout_sec, connect=self.connect_timeout_sec)
            async with httpx.AsyncClient(timeout=timeout) as client:
                offset = 0
                last_mode: str | None = None
                while offset < len(pairs):
                    batch = pairs[offset : offset + self.max_pairs_per_call]
                    response = await client.post(
                        f"{self.server_url}/rerank",
                        json={"pairs": batch, "instruction": self.instruction},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    batch_scores = payload.get("scores") or []
                    if len(batch_scores) != len(batch):
                        raise ValueError("Reranker returned mismatched score count")
                    scores.extend(float(s) for s in batch_scores)
                    last_mode = str(payload.get("mode") or "unknown")
                    offset += self.max_pairs_per_call
                if last_mode is not None:
                    self._last_backend_mode = last_mode
        except (httpx.ConnectError, httpx.ConnectTimeout):
            self._server_unavailable_until = time.monotonic() + self.fallback_cooldown_sec
            self._last_backend_mode = "client_skipped"
            return chunks
        except Exception:
            self._last_backend_mode = "client_skipped_transient"
            return chunks

        ranked = sorted(
            zip(chunks, scores),
            key=lambda item: item[1],
            reverse=True,
        )
        reordered = [
            RetrievalChunk(chunk=c.chunk, score=score) for c, score in ranked
        ]
        if top_k is not None:
            reordered = reordered[:top_k]
        return reordered
