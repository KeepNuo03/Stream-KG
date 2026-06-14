"""实体抽取器（Phase 2/3）。

P3-R-020 重写要点：
- canonicalize_label：大小写 / 标点 / 全半角统一后产出 canonical key；
- derive_entity_id：相同 canonical → 完全相同的确定性 ID，让 mention_count 自然累加；
- 强化噪声黑名单：英文 4-6 字符段的常见噪声词（Provided/Google/News/Schema/Pydantic 等）
  按"是否真实出现在 paper 术语中"判定；
- infer_entity_type：粗粒度规则（person / organization / method / metric / dataset / concept）
  减少 OnlineResolver 的类型不兼容拒绝。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable

from stream_kg.kg.models import ChunkRecord, EntityMention, EntityType, new_mention_id

_EN_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "in",
        "on",
        "at",
        "to",
        "by",
        "as",
        "is",
        "it",
        "be",
        "we",
        "he",
        "she",
        "do",
        "if",
        "so",
        "no",
        "up",
        "out",
        "off",
        "per",
        "via",
        "etc",
        "ie",
        "eg",
        "et",
        "al",
        "fig",
        "eq",
        "ref",
        "for",
        "with",
        "this",
        "that",
        "from",
        "into",
        "using",
        "use",
        "used",
        "uses",
        "user",
        "users",
        "data",
        "system",
        "model",
        "models",
        "method",
        "methods",
        "paper",
        "work",
        "based",
        "also",
        "such",
        "than",
        "then",
        "when",
        "where",
        "which",
        "while",
        "have",
        "has",
        "had",
        "can",
        "may",
        "will",
        "not",
        "are",
        "was",
        "were",
        "been",
        "being",
        "our",
        "their",
        "your",
        "its",
        "all",
        "any",
        "each",
        "other",
        "more",
        "most",
        "some",
        "very",
        "only",
        "just",
        "like",
        "well",
        "even",
        "much",
        "many",
        "new",
        "one",
        "two",
        "first",
        "second",
        "domain",
        "approach",
        "result",
        "results",
        "show",
        "shows",
        "shown",
        "section",
        "figure",
        "table",
        "appendix",
        "abstract",
        "introduction",
        "conclusion",
        "overview",
        "process",
        "example",
        "examples",
        "case",
        "cases",
        "task",
        "tasks",
        "type",
        "types",
        "set",
        "sets",
        "time",
        "times",
        "way",
        "ways",
        "part",
        "parts",
        "key",
        "keys",
        "value",
        "values",
        "code",
        "file",
        "files",
        "line",
        "lines",
        "page",
        "pages",
        "text",
        "word",
        "words",
        "term",
        "terms",
        "name",
        "names",
        "form",
        "forms",
        "info",
        "information",
        "number",
        "numbers",
        "level",
        "levels",
        "state",
        "states",
        "step",
        "steps",
        "item",
        "items",
        "list",
        "lists",
        "node",
        "nodes",
        "edge",
        "edges",
        "graph",
        "vector",
        "vectors",
        "index",
        "query",
        "search",
        "input",
        "output",
        "function",
        "class",
        "object",
        "module",
        "package",
        "library",
        "version",
        "config",
        "default",
        "true",
        "false",
        "null",
        "none",
        "http",
        "https",
        "www",
        "com",
        "org",
        "net",
        "design",
        "network",
        "networks",
        "detection",
        "object",
        "objects",
        "image",
        "images",
        "learning",
        "training",
        "feature",
        "features",
        "layer",
        "layers",
        "block",
        "blocks",
        "structure",
        "framework",
        "algorithm",
        "algorithms",
        "application",
        "applications",
        "processing",
        "representation",
        "performance",
        "accuracy",
        "quality",
        "content",
        "context",
        "analysis",
        "evaluation",
        "comparison",
        "problem",
        "solution",
        "solutions",
        "component",
        "components",
        "resource",
        "resources",
        "environment",
        "control",
        "memory",
        "storage",
        "service",
        "services",
        "platform",
        "device",
        "devices",
        "pattern",
        "patterns",
        "language",
        "languages",
        "vision",
        "speech",
        "audio",
        "video",
        "sequence",
        "sequences",
    }
)

_GRAPH_GENERIC_NOUNS = _EN_STOPWORDS

# R-020：英文「看似术语但实际无信息量」的噪声黑名单。
# 这些词大小写混合 / 首字母大写 / 长度 > 4，会绕过 _is_valid_surface，但语义价值很低。
_ENGLISH_NOISE_TOKENS = frozenset(
    {
        "provided",
        "google",
        "news",
        "calling",
        "schema",
        "pydantic",
        "redis",
        "proper",
        "working",
        "attribution",
        "planning",
        "memory",
        "tools",
        "results",
        "table",
        "section",
        "appendix",
        "abstract",
        "introduction",
        "conclusion",
        "figure",
        "figures",
        "tables",
        "github",
        "twitter",
        "openai",
        "anthropic",
        "company",
        "team",
        "authors",
        "author",
        "thanks",
        "acknowledg",
        "license",
        "copyright",
        "hereby",
        "hereto",
        "hereof",
        "wherein",
        "whereby",
        "previous",
        "current",
        "future",
        "general",
        "specific",
        "various",
        "several",
        "multiple",
        "single",
        "common",
        "standard",
        "simple",
        "complex",
        "basic",
        "advanced",
        "primary",
        "secondary",
        "internal",
        "external",
        "global",
        "local",
        "additional",
        "available",
        "important",
        "necessary",
        "potential",
        "possible",
        "appropriate",
        "effective",
        "efficient",
        "optimal",
        "significant",
        "relevant",
        "different",
        "similar",
        "various",
        "popular",
        "recent",
        "modern",
        "traditional",
        "useful",
        "robust",
        "scalable",
        "novel",
        "real",
        "true",
        "false",
        "actual",
        "typical",
        "normal",
        "regular",
        "default",
        "custom",
        "manual",
        "automatic",
        "dynamic",
        "static",
        "online",
        "offline",
        "open",
        "closed",
        "early",
        "late",
        "fast",
        "slow",
        "high",
        "low",
        "large",
        "small",
        "long",
        "short",
        "deep",
        "shallow",
        "hard",
        "soft",
        "wide",
        "narrow",
        "full",
        "empty",
        "rich",
        "poor",
        "rare",
        "free",
        "fair",
        "easy",
        "hard",
        "good",
        "bad",
        "best",
        "worst",
        "better",
        "worse",
    }
)


def canonicalize_label(label: str) -> str:
    """生成统一的 entity 主键：去标点 / 大小写 / 全半角 / 多空白。

    中文保留原字符；英文一律 lowercase 并合并空白。
    """
    if not label:
        return ""
    text = unicodedata.normalize("NFKC", label).strip()
    if not text:
        return ""
    # 去除前后标点，但保留 token 内 - / _
    text = re.sub(r"^[\s\W_]+|[\s\W_]+$", "", text, flags=re.UNICODE)
    if not text:
        return ""
    if re.search(r"[\u4e00-\u9fff]", text):
        return text.strip()
    return text.lower()


def derive_entity_id(canonical: str, entity_type: str) -> str:
    """根据 canonical surface + type 生成确定性 ID。

    相同 canonical → 相同 ID → mention_count 自然累加。
    用前缀加 hash 缩短：`ent_<8 hex>`。
    """
    raw = f"{entity_type}:{canonical}"
    return "ent_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def infer_entity_type(surface: str) -> EntityType:
    """从 surface 形态推断 entity 粗粒度类型。

    规则（保守，宁少勿错）：
    - 含中文 → concept
    - 全大写 2-6 字符 → method（视为缩写，如 BERT / GPT / RNN）
    - 大小写混合且长度 >= 4 → method（如 Transformer / Pydantic）
    - 含 - 或 _ → method
    - 纯小写 → concept
    """
    if not surface:
        return "concept"
    if re.search(r"[\u4e00-\u9fff]", surface):
        return "concept"
    if surface.isupper() and 2 <= len(surface) <= 6:
        return "method"
    has_upper = any(c.isupper() for c in surface)
    has_lower = any(c.islower() for c in surface)
    if (has_upper and has_lower) or "-" in surface or "_" in surface:
        return "method"
    return "concept"


def is_english_noise(label: str) -> bool:
    """判定英文 label 是否在 R-020 噪声黑名单中。"""
    if not label:
        return True
    text = label.strip().lower()
    if not text:
        return True
    if text in _ENGLISH_NOISE_TOKENS:
        return True
    # 词干 prefix 命中（acknowledg → acknowledge / acknowledgement / acknowledgments）
    for noise in _ENGLISH_NOISE_TOKENS:
        if len(noise) >= 7 and text.startswith(noise):
            return True
    return False


class EntityExtractor:
    """从 chunk 文本中提取候选实体 mention。"""

    def __init__(self, *, per_chunk_limit: int = 18, min_english_len: int = 4) -> None:
        self.per_chunk_limit = per_chunk_limit
        self.min_english_len = min_english_len
        self._english_pattern = re.compile(r"\b[A-Za-z][A-Za-z0-9_\-]{2,}\b")
        self._chinese_pattern = re.compile(r"[\u4e00-\u9fff]{2,8}")
        self._zh_stopwords = {
            "我们",
            "你们",
            "他们",
            "这个",
            "那个",
            "这样",
            "可以",
            "进行",
            "为了",
            "如果",
            "以及",
            "然后",
            "因为",
            "所以",
            "文档",
            "系统",
            "问题",
            "实现",
            "使用",
            "当前",
            "过程",
            "结果",
            "用户",
            "数据",
            "方法",
            "模型",
            "研究",
            "工作",
            "本文",
            "其中",
            "通过",
            "主要",
            "相关",
            "不同",
            "一种",
            "一个",
            "这些",
            "那些",
            "具有",
            "表示",
            "采用",
            "提出",
            "基于",
            "方面",
            "情况",
            "部分",
            "领域",
            "任务",
            "效果",
            "性能",
            "实验",
            "分析",
            "介绍",
            "概述",
            "总结",
            "结论",
        }

    def extract(self, chunks: Iterable[ChunkRecord]) -> list[EntityMention]:
        """批量抽取 mentions。"""
        mentions: list[EntityMention] = []
        for chunk in chunks:
            mentions.extend(self.extract_from_chunk(chunk))
        return mentions

    def extract_from_chunk(self, chunk: ChunkRecord) -> list[EntityMention]:
        """从单个 chunk 抽取 mentions。"""
        content = chunk.content or ""
        if not content.strip():
            return []

        seen_spans: set[tuple[int, int]] = set()
        seen_surface: set[str] = set()
        out: list[EntityMention] = []

        def append_match(surface_form: str, char_start: int, char_end: int, entity_type: EntityType) -> None:
            if len(out) >= self.per_chunk_limit:
                return
            span = (char_start, char_end)
            if span in seen_spans:
                return
            surface = surface_form.strip()
            if not surface:
                return
            normalized = surface.lower()
            if normalized in seen_surface:
                return
            if not self._is_valid_surface(surface, entity_type):
                return
            mention = EntityMention(
                mention_id=new_mention_id(),
                doc_id=chunk.doc_id,
                chunk_id=chunk.chunk_id,
                surface_form=surface,
                entity_type=entity_type,
                char_start=char_start,
                char_end=char_end,
                context_snippet=self._build_context(content, char_start, char_end),
            )
            out.append(mention)
            seen_spans.add(span)
            seen_surface.add(normalized)

        for match in self._english_pattern.finditer(content):
            surface = match.group(0)
            ent_type: EntityType = self._english_entity_type(surface)
            append_match(surface, match.start(), match.end(), ent_type)

        for match in self._chinese_pattern.finditer(content):
            surface = match.group(0)
            append_match(surface, match.start(), match.end(), "concept")

        return out

    def _is_valid_surface(self, surface: str, entity_type: EntityType) -> bool:
        if entity_type == "concept" and surface in self._zh_stopwords:
            return False
        if re.search(r"[\u4e00-\u9fff]", surface):
            # 中文 surface：至少 2 字（pattern 已强制），但纯中文标点不算
            stripped = re.sub(r"\s+", "", surface)
            if len(stripped) < 2:
                return False
            return True
        if re.search(r"[A-Za-z]", surface):
            lowered = surface.lower()
            if lowered in _EN_STOPWORDS:
                return False
            if is_english_noise(lowered):
                return False
            if len(lowered) < self.min_english_len:
                return False
            if lowered.isdigit():
                return False
            has_upper = any(c.isupper() for c in surface)
            has_mixed_case = has_upper and any(c.islower() for c in surface)
            is_acronym = surface.isupper() and len(surface) >= 2
            # 纯小写英文：至少 6 字符，过滤 the/and/data/code 等泛词
            if surface.islower() and len(surface) < 6:
                return False
            # 短 token 必须有大小写混合或全大写缩写，否则视为噪声
            if len(surface) <= 4 and not (has_mixed_case or is_acronym):
                return False
        return True

    def _english_entity_type(self, surface: str) -> EntityType:
        if any(c.isupper() for c in surface) or "-" in surface or "_" in surface:
            return "method"
        return "concept"

    def _build_context(self, text: str, start: int, end: int, window: int = 100) -> str:
        """抽取 mention 周边上下文。"""
        left = max(0, start - window)
        right = min(len(text), end + window)
        return text[left:right].strip()

    def is_plausible_label(self, label: str) -> bool:
        """判断标签是否适合在图谱中展示（与抽取规则一致）。"""
        surface = (label or "").strip()
        if not surface:
            return False
        ent_type = self._english_entity_type(surface)
        return self._is_valid_surface(surface, ent_type)


def is_plausible_entity_label(label: str) -> bool:
    """模块级便捷函数：过滤图谱噪声节点（与抽取规则一致）。"""
    return EntityExtractor().is_plausible_label(label)


def is_displayable_entity_label(label: str) -> bool:
    """图谱展示用软过滤：去掉停用词/过短词/R-020 噪声词，保留可读术语。"""
    surface = (label or "").strip()
    if not surface:
        return False
    if re.search(r"[\u4e00-\u9fff]", surface):
        stripped = re.sub(r"\s+", "", surface)
        return len(stripped) >= 2
    lowered = surface.lower()
    if lowered in _EN_STOPWORDS:
        return False
    if is_english_noise(lowered):
        return False
    if len(lowered) < 3:
        return False
    if lowered.isdigit():
        return False
    if lowered.islower() and len(lowered) < 5:
        return False
    return True


def is_technical_entity_label(label: str) -> bool:
    """判断标签是否像技术术语（非泛化英文名词）。"""
    surface = (label or "").strip()
    if not surface:
        return False
    if re.search(r"[\u4e00-\u9fff]", surface):
        return len(surface) >= 2
    has_upper = any(c.isupper() for c in surface)
    has_mixed = has_upper and any(c.islower() for c in surface)
    is_acronym = surface.isupper() and len(surface) >= 2
    if has_mixed or is_acronym or "-" in surface or "_" in surface:
        return True
    lowered = surface.lower()
    if lowered in _GRAPH_GENERIC_NOUNS:
        return False
    if surface.islower() and len(surface) >= 7:
        return True
    return False


def is_meaningful_graph_label(label: str) -> bool:
    """图谱节点展示：可读 + 术语优先，过滤 design/network 等泛词。"""
    return is_displayable_entity_label(label) and is_technical_entity_label(label)
