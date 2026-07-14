"""全链路追踪初始化：开发期 LangSmith + 生产期 OpenTelemetry。

用法:
    from super_agent.tracing import setup_tracing

    # 应用启动时调用一次
    setup_tracing()

    # 在需要追踪的函数上使用 @traceable（LangSmith 用）
    # 或用 tracer.start_as_current_span(name)（OTel 用）
"""

from __future__ import annotations

import logging
import os

from super_agent.config import settings

logger = logging.getLogger(__name__)

# 全局 OTel tracer，供整个应用使用
tracer: "trace.Tracer"  # type: ignore[name-defined]  # noqa: F821


def setup_tracing() -> None:
    """根据配置初始化追踪系统。"""
    global tracer

    if settings.tracing.enable_langsmith:
        _setup_langsmith()

    if settings.tracing.enable_otel:
        _setup_otel()
    else:
        # Fallback: no-op tracer
        from opentelemetry import trace as _trace

        tracer = _trace.get_tracer("super-agent")


def _setup_langsmith() -> None:
    """配置 LangSmith 开发期追踪。

    LangSmith 是 LangChain 官方的调试平台，能展示每条 RAG 请求的完整链路：
      QueryProcessor → Retriever(向量+BM25) → RRF融合 → AnswerGenerator → LLM调用
    每条 trace 可查看每一步的输入输出内容，适合开发调试检索效果。
    """
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_PROJECT", settings.tracing.langsmith_project)
    if settings.tracing.langsmith_api_key:
        os.environ.setdefault("LANGCHAIN_API_KEY", settings.tracing.langsmith_api_key)
    logger.info("LangSmith tracing enabled, project=%s", settings.tracing.langsmith_project)


def _setup_otel() -> None:
    """配置 OpenTelemetry 生产期全链路追踪。

    OTel + Jaeger 追踪每个请求贯穿所有服务的完整路径：
      FastAPI → QueryProcessor → Embedder → VectorStore → ESClient → LLMClient → AuditLogger
    每条 trace 记录每步耗时和状态码，适合线上性能监控和问题排查。
    """
    from opentelemetry import trace as _trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    global tracer

    provider = TracerProvider()
    exporter = OTLPSpanExporter(
        endpoint=settings.tracing.otel_exporter,
        insecure=True,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    _trace.set_tracer_provider(provider)
    tracer = _trace.get_tracer(settings.tracing.otel_service_name)
    logger.info("OTel tracing enabled, exporter=%s", settings.tracing.otel_exporter)
