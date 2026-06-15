"""LLM 客户端与 prompt 工具（P3-X · Phase A 起新增）。"""

from stream_kg.llm.deepseek_client import DeepSeekClient, DeepSeekError

__all__ = ["DeepSeekClient", "DeepSeekError"]
