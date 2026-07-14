"""可观测性模块：Prometheus 聚合指标 + LangSmith 链路追踪 + OpenTelemetry 全链路追踪。"""

from __future__ import annotations

from super_agent.tracing.metrics import (
    CONTENT_TYPE_LATEST,
    GenerationTimer,
    RetrievalTimer,
    generate_latest,
    rag_queries_total,
)
from super_agent.tracing.setup import setup_tracing, tracer

__all__ = [
    "CONTENT_TYPE_LATEST",
    "GenerationTimer",
    "RetrievalTimer",
    "generate_latest",
    "rag_queries_total",
    "setup_tracing",
    "tracer",
]
