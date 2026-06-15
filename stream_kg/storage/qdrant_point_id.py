"""Qdrant point ID 规范化。

Qdrant 仅接受 unsigned integer 或 UUID 作为 point id。
R-020 后实体主键为 `ent_<hex>`（derive_entity_id），写入向量库前需映射为确定性 UUID。
"""

from __future__ import annotations

import uuid

# 固定 namespace：同一 entity_id 永远映射到同一 Qdrant point。
_ENTITY_POINT_NAMESPACE = uuid.UUID("a3f2c8e1-9b4d-5e6f-8a7c-1d2e3f4a5b6c")


def is_valid_qdrant_point_id(point_id: str) -> bool:
    """判断字符串是否已是 Qdrant 接受的 point id。"""
    if not point_id:
        return False
    try:
        uuid.UUID(point_id)
        return True
    except ValueError:
        return point_id.isdigit()


def entity_id_to_qdrant_point_id(entity_id: str) -> str:
    """将业务 entity_id 转为 Qdrant 可接受的 point id。"""
    if is_valid_qdrant_point_id(entity_id):
        return entity_id
    return str(uuid.uuid5(_ENTITY_POINT_NAMESPACE, entity_id))
