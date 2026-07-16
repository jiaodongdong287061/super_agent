from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_dotenv() -> None:
    """根据 SA_ENV 加载对应的 .env 文件到进程环境变量（不覆盖已有值）"""
    env_name = os.environ.get("SA_ENV", "dev")
    env_file = _PROJECT_ROOT / f".env.{env_name}"
    if not env_file.exists():
        env_file = _PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


# 模块加载时一次性注入环境变量
_load_dotenv()


class LLMConfig(BaseSettings):
    oneapi_base_url: str = "http://localhost:3000/v1"
    oneapi_api_key: str = ""
    default_model: str = "gpt-4o"
    default_temperature: float = 0.7
    max_tokens: int = 4096
    request_timeout: int = 60
    router_model: str = ""
    planner_model: str = ""
    supervisor_model: str = ""
    code_model: str = ""

    model_config = SettingsConfigDict(env_prefix="SA_LLM_")


class EmbeddingConfig(BaseSettings):
    provider: Literal["api"] = "api"
    api_url: str = ""
    api_key: str = ""
    api_model: str = ""
    api_batch_size: int = 64

    model_config = SettingsConfigDict(env_prefix="SA_EMBEDDING_")


class VectorStoreConfig(BaseSettings):
    provider: Literal["chroma", "milvus", "qdrant"] = "chroma"
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    chroma_persist_dir: str = "./data/chroma"
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "super_agent_docs"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "super_agent_docs"
    qdrant_api_key: str = ""
    qdrant_vector_size: int = 1024
    qdrant_distance: Literal["COSINE", "EUCLID", "DOT"] = "COSINE"
    default_top_k: int = 5

    model_config = SettingsConfigDict(env_prefix="SA_VECTOR_")


class RedisConfig(BaseSettings):
    url: str = "redis://localhost:6379"
    db: int = 0
    password: str = ""
    short_memory_ttl: int = 3600
    pool_size: int = 10

    model_config = SettingsConfigDict(env_prefix="SA_REDIS_")


class MySQLConfig(BaseSettings):
    host: str = "localhost"
    port: int = 3306
    username: str = "root"
    password: str = ""
    database: str = "super_agent"
    pool_size: int = 10
    echo_sql: bool = False

    @property
    def dsn(self) -> str:
        return f"mysql+asyncmy://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}"

    model_config = SettingsConfigDict(env_prefix="SA_MYSQL_")


class SandboxConfig(BaseSettings):
    docker_host: str = "unix:///var/run/docker.sock"
    default_profile: str = "code-exec"
    cleanup_on_exit: bool = True
    max_concurrent_containers: int = 5

    model_config = SettingsConfigDict(env_prefix="SA_SANDBOX_")


class TracingConfig(BaseSettings):
    langsmith_api_key: str = ""
    langsmith_project: str = "super-agent"
    enable_langsmith: bool = True
    enable_otel: bool = False
    otel_exporter: str = "http://localhost:4317"
    otel_service_name: str = "super-agent"

    model_config = SettingsConfigDict(env_prefix="SA_TRACING_")


class OCRConfig(BaseSettings):
    enabled: bool = True
    use_gpu: bool = False
    lang: str = "ch"
    page_dpi: int = 200
    text_threshold: float = 0.1

    model_config = SettingsConfigDict(env_prefix="SA_OCR_")


class RAGConfig(BaseSettings):
    enable_metrics: bool = True
    enable_audit: bool = True
    enable_query_rewrite: bool = True
    enable_query_expansion: bool = False  # 查询扩展 → 将 query 扩展为多个同义变体分别检索，扩大召回面（需 LLM 调用，耗时+耗 token）
    enable_intent_classification: bool = True  # 意图分类 → 识别 query 类型（qa/summarize/instruction），用于下游调整生成 prompt
    enable_bm25_hybrid: bool = False  # 启用 ES BM25 混合检索（向量 + 关键词双路 → RRF 融合）
    chunker_provider: str = "semantic"
    chunker_use_llm: bool = False
    max_chunk_size: int = 500
    overlap_ratio: float = 0.15
    default_system_prompt: str = ""

    model_config = SettingsConfigDict(env_prefix="SA_RAG_")


class ESConfig(BaseSettings):
    """Elasticsearch 配置（用于 BM25 混合检索）。"""
    hosts: str = "http://localhost:9200"
    username: str = ""
    password: str = ""
    index_name: str = "super_agent_docs"
    chunk_batch_size: int = 100
    ca_certs: str = ""

    model_config = SettingsConfigDict(env_prefix="SA_ES_")


class ServerConfig(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    cors_origins: list[str] = ["*"]
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    model_config = SettingsConfigDict(env_prefix="SA_SERVER_")


class SSOConfig(BaseSettings):
    """OAuth2 SSO 登录配置（授权码模式）。

    接入授权中心（OAuth2 Server），通过标准的 Authorization Code 流程获取用户身份。
    enable=false 时跳过认证（开发/测试环境）。

    JWT 认证: 使用 SSO 签发的 Ruoyi JWT (HS512) 作为会话凭证。
    可选 Redis 验证: 查询 SSO 的 Redis 确认 token 未被注销。
    """
    enable: bool = False
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:8000/auth/callback"
    auth_base_url: str = "http://localhost:8081"
    frontend_url: str = "http://localhost:8000"
    whitelist: list[str] = ["/health", "/metrics", "/docs", "/openapi.json", "/auth/login", "/auth/callback"]
    jwt_secret: str = "abcdefghijklmnopqrstuvwxyz"  # !!! 建议 4 的倍数长度（如 24/28/32 字符），否则 Java DatatypeConverter 会丢弃尾部
    session_max_age: int = 43200  # 12小时
    redis_enabled: bool = True
    redis_url: str = "redis://localhost:6379/0"
    redis_password: str = ""
    redis_key_prefix: str = "login_tokens:"

    model_config = SettingsConfigDict(env_prefix="SA_SSO_")


class Settings(BaseSettings):
    llm: LLMConfig = LLMConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    vector_store: VectorStoreConfig = VectorStoreConfig()
    redis: RedisConfig = RedisConfig()
    mysql: MySQLConfig = MySQLConfig()
    sandbox: SandboxConfig = SandboxConfig()
    tracing: TracingConfig = TracingConfig()
    sso: SSOConfig = SSOConfig()
    server: ServerConfig = ServerConfig()
    ocr: OCRConfig = OCRConfig()
    rag: RAGConfig = RAGConfig()
    es: ESConfig = ESConfig()
    env: Literal["dev", "prod"] = "dev"

    model_config = SettingsConfigDict(env_prefix="SA_")

    @model_validator(mode="after")
    def _rebuild_sub_configs(self) -> Settings:
        """Re-initialize sub-configs so they pick up env vars at runtime."""
        self.llm = LLMConfig()
        self.embedding = EmbeddingConfig()
        self.vector_store = VectorStoreConfig()
        self.redis = RedisConfig()
        self.mysql = MySQLConfig()
        self.sandbox = SandboxConfig()
        self.tracing = TracingConfig()
        self.sso = SSOConfig()
        self.server = ServerConfig()
        self.ocr = OCRConfig()
        self.rag = RAGConfig()
        self.es = ESConfig()
        return self


class ConfigurationError(Exception):
    pass


def validate_settings(s: Settings) -> None:
    errors = []
    if s.env == "prod":
        if not s.llm.oneapi_api_key:
            errors.append("SA_LLM_ONEAPI_API_KEY is required in production")
        if not s.mysql.password:
            errors.append("SA_MYSQL_PASSWORD is required in production")
        if not s.redis.password:
            errors.append("SA_REDIS_PASSWORD is required in production")
        if s.server.cors_origins == ["*"]:
            errors.append("CORS wildcard not allowed in production")
        if s.server.log_level == "DEBUG":
            errors.append("DEBUG log level not recommended in production")
    if s.ocr.use_gpu:
        logger.warning("OCR GPU mode requested — ensure paddlepaddle-gpu is installed and GPU is available")
    if errors:
        raise ConfigurationError("\n".join(errors))


settings = Settings()
