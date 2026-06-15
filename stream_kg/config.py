"""Application configuration via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load settings from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    data_dir: str = "./data"

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000"

    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection_chunks: str = "chunks"
    qdrant_collection_entities: str = "entities"

    sqlite_path: str = "./data/meta.db"
    graph_path: str = "./data/graph.pkl"

    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_model_local_path: str = ""
    embedding_download_source: str = "modelscope"  # modelscope | huggingface | auto
    hf_endpoint: str = "https://hf-mirror.com"
    embedding_server_url: str = "http://localhost:8081"
    embedding_dim: int = 1024
    embedding_batch_size: int = 32
    embedding_request_timeout_sec: float = 60.0
    embedding_connect_timeout_sec: float = 2.0
    embedding_fallback_cooldown_sec: int = 5

    reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"
    reranker_model_local_path: str = ""
    reranker_download_source: str = "modelscope"
    reranker_server_url: str = "http://localhost:8082"
    reranker_enabled: bool = False
    # 单次 rerank 单段耗时较高（CPU 上 Qwen3-0.6B 每对 1-3s），不能像 embed 那样快
    reranker_request_timeout_sec: float = 90.0
    reranker_connect_timeout_sec: float = 2.0
    reranker_fallback_cooldown_sec: int = 5
    reranker_max_pairs_per_call: int = 8
    reranker_instruction: str = (
        "Given a user query, retrieve the most relevant document passages that "
        "answer the query in Chinese or English."
    )

    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_api_base: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.1

    mineru_device: str = "cuda"
    mineru_output_dir: str = "./data/parsed"
    mineru_cli: str = "mineru"
    mineru_backend: str = "pipeline"
    mineru_source: str = "modelscope"
    mineru_timeout_sec: int = 1800
    pdf_parse_mode: str = "fast"  # fast: 仅 pypdf；quality: 必要时回退 MinerU

    web_fetch_timeout_sec: float = 20.0
    web_fetch_cookie: str = ""

    chunk_size: int = 512
    chunk_overlap: int = 64

    resolve_alpha: float = 0.60
    resolve_beta: float = 0.25
    resolve_gamma: float = 0.15
    resolve_threshold: float = 0.82
    resolve_top_k: int = 20

    retrieval_top_k: int = 10
    rerank_top_k: int = 5
    graph_hop: int = 2
    rag_max_context_chars_per_chunk: int = 1200
    rag_min_relevance_score: float = 0.20
    rag_low_relevance_margin: float = 0.05
    rag_evidence_fallback_min_score: float = 0.30

    ingest_poll_interval_sec: int = 2

    feature_kg_enabled: bool = False
    kg_max_mentions_per_doc: int = 120
    kg_candidate_search_concurrency: int = 8
    feature_reranker_enabled: bool = False
    feature_graph_router_enabled: bool = False

    # === LLM KG 抽取（P3-X · Phase A，见 docs/planning/14-kg-llm-execution-plan.md） ===
    # 默认 False：上传时如果 feature_kg_enabled=True，仍走旧规则抽取（行为不变）；
    # 用户通过 POST /api/v1/documents/{doc_id}/extract-kg 显式触发时**强制用 LLM**，
    # 不受此开关约束（手动触发即明确意图）。
    # 把此项设为 True 时：feature_kg_enabled=True 的上传流程会自动走 LLM 抽取（耗钱，慎开）。
    feature_kg_use_llm: bool = True
    # 单个文档内并发抽取的 chunk 数（asyncio.Semaphore）。E5 决策：5 起步。
    kg_extraction_concurrency: int = 5
    # 单 chunk 抽取失败重试次数（13 文档 §2.5）。
    kg_extraction_max_retries: int = 3
    # 每次 LLM 调用 max_tokens 上限；典型抽取输出 200-900 token，2048 留富余。
    kg_extraction_max_tokens: int = 2048
    # 单 chunk 抽取超时（秒）。PoC 实测 5-7s，给 30s 富余防偶发慢响应。
    kg_extraction_timeout_sec: float = 30.0
    # 月度 LLM 费用上限（CNY）；超额暂停（13 文档 §6 风险表）。
    # PoC 实测：100 chunk 论文 ~0.17 CNY → 默认 10 元能跑约 6000 chunk。
    kg_budget_yuan: float = 10.0


settings = Settings()
