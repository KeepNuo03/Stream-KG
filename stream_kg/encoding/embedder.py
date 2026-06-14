"""Embedding 客户端。

优先调用本地 embedding server；
若服务不可用，则用确定性伪向量兜底，保证开发阶段主链路可跑通。
"""

from __future__ import annotations

import hashlib
import math
import random
import time

import httpx

from stream_kg.config import settings


def _deterministic_vector(text: str, dim: int) -> list[float]:
    """生成确定性伪向量（仅开发兜底，不用于线上质量评估）。"""
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)
    vector = [rng.uniform(-1, 1) for _ in range(dim)]
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


class Embedder:
    """带失败兜底的向量化客户端。"""

    def __init__(
        self,
        *,
        server_url: str,
        dim: int,
        timeout_sec: float = 2.0,
        connect_timeout_sec: float = 0.3,
        fallback_cooldown_sec: int = 60,
        batch_size: int = 32,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.dim = dim
        self.timeout_sec = timeout_sec
        self.connect_timeout_sec = connect_timeout_sec
        self.fallback_cooldown_sec = fallback_cooldown_sec
        self.batch_size = max(batch_size, 1)
        # 失败熔断窗口：在窗口内直接走本地伪向量，避免每次都等待网络超时。
        self._server_unavailable_until = 0.0
        self._last_backend_mode: str = "unknown"

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量向量化（按 batch_size 分段请求）。"""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), self.batch_size):
            batch = texts[offset : offset + self.batch_size]
            vectors.extend(await self._embed_batch_once(batch))
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        """单条向量化（复用 batch 接口）。"""
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def _embed_batch_once(self, texts: list[str]) -> list[list[float]]:
        if time.monotonic() < self._server_unavailable_until:
            return [_deterministic_vector(text, self.dim) for text in texts]
        try:
            timeout = httpx.Timeout(self.timeout_sec, connect=self.connect_timeout_sec)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self.server_url}/embed", json={"texts": texts})
                response.raise_for_status()
                payload = response.json()
                vectors = payload.get("vectors", [])
                if len(vectors) != len(texts):
                    raise ValueError("Embedding server returned mismatched vector count")
                self._server_unavailable_until = 0.0
                self._last_backend_mode = str(payload.get("mode") or "unknown")
                return vectors
        except (httpx.ConnectError, httpx.ConnectTimeout):
            # 服务真不可达：才进入熔断窗口，避免每个 batch 都等满 read timeout。
            self._server_unavailable_until = time.monotonic() + self.fallback_cooldown_sec
            self._last_backend_mode = "client_pseudo"
            return [_deterministic_vector(text, self.dim) for text in texts]
        except Exception:
            # HTTP 4xx/5xx / 单次解析失败：不锁定熔断窗口。
            # 仅本次降级到伪向量，下次请求继续打 server。
            # 否则一段超长 chunk 引发 500 就会拖垮整个进程的 query 链路。
            self._last_backend_mode = "client_pseudo_transient"
            return [_deterministic_vector(text, self.dim) for text in texts]

    async def probe_backend(self) -> dict[str, str]:
        """探测 embedding 服务状态。"""
        try:
            timeout = httpx.Timeout(5.0, connect=self.connect_timeout_sec)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(f"{self.server_url}/health")
                response.raise_for_status()
                data = response.json()
                mode = str(data.get("mode") or "unknown")
                self._last_backend_mode = mode
                return {
                    "status": "ok",
                    "mode": mode,
                    "device": str(data.get("device") or ""),
                }
        except Exception:
            return {"status": "down", "mode": self._last_backend_mode, "device": ""}
