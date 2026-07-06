from __future__ import annotations

import time

# Prometheus 监控指标
# Prometheus 是开源监控系统，该模块暴露 /metrics 端点供 Prometheus 抓取。
# 数据存在内存中，无人抓取时不影响系统运行。
# 后续配合 Grafana 可实现 QPS/延迟 P99/错误率 可视化看板。
from prometheus_client import Counter, Histogram, generate_latest  # noqa: F401  re-exported for main.py

from super_agent.knowledge.models import Chunk

CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

# RAG 查询总量（按成功/失败标签区分）
rag_queries_total = Counter("rag_queries_total", "Total RAG queries", ["status"])
# 向量检索耗时分布
rag_retrieval_duration = Histogram(
    "rag_retrieval_duration_seconds",
    "Retrieval latency in seconds",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
# LLM 生成耗时分布
rag_generation_duration = Histogram(
    "rag_generation_duration_seconds",
    "Generation latency in seconds",
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0),
)
# 每次查询返回的 chunk 数量分布
rag_retrieved_chunks = Histogram(
    "rag_retrieved_chunks",
    "Number of chunks retrieved per query",
    buckets=(1, 3, 5, 10, 20, 50),
)


class RetrievalTimer:
    """Context manager that records retrieval duration and chunk count."""

    def __init__(self) -> None:
        self._start = 0.0

    def __enter__(self) -> RetrievalTimer:
        self._start = time.monotonic()
        return self

    def __exit__(self, *args: object) -> None:
        elapsed = time.monotonic() - self._start
        rag_retrieval_duration.observe(elapsed)

    def record_chunks(self, chunks: list[Chunk]) -> None:
        rag_retrieved_chunks.observe(len(chunks))


class GenerationTimer:
    """Context manager that records generation duration."""

    def __init__(self) -> None:
        self._start = 0.0

    def __enter__(self) -> GenerationTimer:
        self._start = time.monotonic()
        return self

    def __exit__(self, *args: object) -> None:
        elapsed = time.monotonic() - self._start
        rag_generation_duration.observe(elapsed)
