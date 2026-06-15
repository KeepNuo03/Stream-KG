/** 知识图谱实体类型 / 关系类型 → 中文展示名 */

export const ENTITY_TYPE_LABELS: Record<string, string> = {
  person: "人物",
  organization: "机构",
  paper: "论文",
  method: "方法",
  concept: "概念",
  dataset: "数据集",
  metric: "指标",
  task: "任务",
  location: "地点",
  time: "时间",
  tool: "工具",
  role: "角色",
  document: "文档",
  entity: "实体",
};

export const RELATION_TYPE_LABELS: Record<string, string> = {
  mentions: "文档提及",
  co_occurs: "共现",
  improves: "改进",
  extends: "扩展",
  contradicts: "对立",
  conflict: "冲突",
  surveys: "综述",
  related: "相关",
  uses: "使用",
  evaluates_on: "评估于",
  authors: "作者",
  part_of: "组成部分",
  proposes: "提出",
  affiliated_with: "隶属于",
  shares_entity: "共享实体",
};

export function localizeEntityType(type: string): string {
  return ENTITY_TYPE_LABELS[type] ?? type;
}

export function localizeRelation(type: string): string {
  return RELATION_TYPE_LABELS[type] ?? type;
}

/** 将 LLM 解释正文里的英文关系名替换为中文（如 co_occurs → 共现） */
export function localizeExplanationText(text: string): string {
  let out = text;
  const entries = Object.entries(RELATION_TYPE_LABELS).sort(
    (a, b) => b[0].length - a[0].length
  );
  for (const [en, zh] of entries) {
    out = out.replace(new RegExp(`「${en}」`, "gi"), `「${zh}」`);
    out = out.replace(new RegExp(`"${en}"`, "gi"), `「${zh}」`);
    out = out.replace(new RegExp(`'${en}'`, "gi"), `「${zh}」`);
    out = out.replace(new RegExp(`\\b${en}\\b`, "gi"), zh);
  }
  return out;
}
