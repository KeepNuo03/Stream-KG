"""知识图谱实体 / 关系类型中文展示名（与前端 kg-labels.ts 对齐）。"""

ENTITY_TYPE_LABELS: dict[str, str] = {
    "person": "人物",
    "organization": "机构",
    "paper": "论文",
    "method": "方法",
    "concept": "概念",
    "dataset": "数据集",
    "metric": "指标",
    "task": "任务",
    "location": "地点",
    "time": "时间",
    "tool": "工具",
    "role": "角色",
    "document": "文档",
    "entity": "实体",
}

RELATION_TYPE_LABELS: dict[str, str] = {
    "mentions": "文档提及",
    "co_occurs": "共现",
    "improves": "改进",
    "extends": "扩展",
    "contradicts": "对立",
    "conflict": "冲突",
    "surveys": "综述",
    "related": "相关",
    "uses": "使用",
    "evaluates_on": "评估于",
    "authors": "作者",
    "part_of": "组成部分",
    "proposes": "提出",
    "affiliated_with": "隶属于",
    "shares_entity": "共享实体",
}


def localize_relation(relation_type: str) -> str:
    return RELATION_TYPE_LABELS.get(relation_type, relation_type)
