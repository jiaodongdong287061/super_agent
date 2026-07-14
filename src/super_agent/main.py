from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from super_agent.config import settings
from super_agent.knowledge.indexer import Indexer
from super_agent.knowledge.models import UserContext
from super_agent.knowledge.generator import AnswerGenerator
from super_agent.tracing.metrics import (  # Prometheus 监控指标，暴露 /metrics 端点，无基础设施时不影响运行
    CONTENT_TYPE_LATEST,
    GenerationTimer,
    RetrievalTimer,
    generate_latest,
    rag_queries_total,
)
from super_agent.tracing import setup_tracing, tracer

logger = logging.getLogger(__name__)


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    filters: dict | None = None
    user: UserContext = Field(default_factory=UserContext)
    system_prompt: str | None = None
    temperature: float | None = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict]
    citations: list[dict] = []
    trace_id: str = ""


class DeleteRequest(BaseModel):
    chunk_ids: list[str] | None = None
    tenant_id: str = ""


class DeleteResponse(BaseModel):
    status: str
    deleted_count: int = 0


class BatchQueryItem(BaseModel):
    query: str
    top_k: int = 5
    filters: dict | None = None
    system_prompt: str | None = None
    temperature: float | None = None


class BatchQueryRequest(BaseModel):
    queries: list[BatchQueryItem]


class BatchQueryResponse(BaseModel):
    results: list[QueryResponse]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=getattr(logging, settings.server.log_level))
    setup_tracing()
    logger.info("Super Agent starting in %s mode", settings.env)
    yield


app = FastAPI(
    title="Super Agent",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Core helpers ──────────────────────────────────────────────


def _build_retriever(user: UserContext):
    """Build retriever based on user context (supports tenant isolation & fan-out)."""
    from super_agent.knowledge.retriever import Retriever
    from super_agent.knowledge.fanout_retriever import FanOutRetriever
    from super_agent.knowledge.stores import get_store, get_all_tenant_stores
    from super_agent.knowledge.embedders import get_embedder

    embedder = get_embedder()

    es_client = None
    if settings.rag.enable_bm25_hybrid:
        from super_agent.knowledge.es_client import ESClient
        es_client = ESClient()

    if user.tenant_id:
        store = get_store(tenant_id=user.tenant_id)
        return Retriever(store=store, embedder=embedder, es_client=es_client)

    stores = get_all_tenant_stores()
    if not stores:
        store = get_store()
        return Retriever(store=store, embedder=embedder, es_client=es_client)
    return FanOutRetriever(stores=stores, embedder=embedder)


def _retrieve_chunks(
    query: str,
    top_k: int,
    retriever,
    filters: dict | None = None,
    user: UserContext | None = None,
) -> list:
    """Execute retrieval with optional Query Expansion → RRF fusion."""
    from super_agent.knowledge.query_processor import QueryProcessor

    qp = QueryProcessor()
    processed = qp.process(query)
    search_query = processed.rewritten

    all_queries = [search_query] + (processed.expansions if processed.expansions else [])
    if len(all_queries) == 1:
        chunks = retriever.retrieve(search_query, top_k=top_k, filters=filters, user=user)
    else:
        from super_agent.knowledge.retriever import reciprocal_rank_fusion
        from super_agent.knowledge.models import SearchResult

        all_results: list[list] = []
        for q in all_queries:
            results = retriever.retrieve(q, top_k=top_k * 2, filters=filters, user=user)
            all_results.append([SearchResult(chunk=c, score=1.0) for c in results])
        fused = reciprocal_rank_fusion(*all_results, k=60)
        chunks = [r.chunk for r in fused[:top_k]]

    return chunks


def _format_sources(chunks: list) -> list[dict]:
    return [
        {
            "chunk_id": c.id,
            "content": c.content[:200],
            "metadata": c.metadata,
            "page_numbers": c.page_numbers,
        }
        for c in chunks
    ]


# ── Endpoints ─────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    """Prometheus 监控指标端点，返回 rag_queries_total / 检索延迟 / 生成延迟等。"""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/rag/query", response_model=QueryResponse)
async def rag_query(req: QueryRequest):
    """RAG 检索 + LLM 答案生成。

    流程：Query 改写 → Embed → 向量检索 → (可选 BM25 + RRF) → (可选 Rerank) → LLM 生成答案 → 审计日志

    参数（QueryRequest body）:
        query: str                    — 用户查询原文
        top_k: int (默认5)            — 返回的文档块数量
        filters: dict | None          — 自定义 metadata 过滤条件
        user: UserContext             — 用户上下文，含 user_id / roles / department / tenant_id
        system_prompt: str | None     — 自定义 LLM 系统提示词
        temperature: float | None     — LLM 温度参数

    租户隔离策略:
        - user.tenant_id 非空 → 仅检索该租户的独立集合（super_agent_docs_{tenant_id}）
        - user.tenant_id 为空 → Fan-out 检索所有租户集合（admin 跨租户搜索）

    返回:
        answer: str                   — LLM 生成的答案文本
        sources: list[dict]           — 检索到的源文档块（chunk_id / content / metadata / page_numbers）
        citations: list[dict]         — 答案中引用的来源（chunk_id / source_doc / page_numbers / content_snippet）
        trace_id: str                 — 追踪 ID（预留，当前为空）
    """
    t_start = time.time()
    try:
        retriever = _build_retriever(req.user)

        # Query understanding + retrieval
        retrieval_timer = RetrievalTimer()
        with retrieval_timer, tracer.start_as_current_span("retrieval") as span:
            chunks = _retrieve_chunks(
                query=req.query, top_k=req.top_k, retriever=retriever,
                filters=req.filters, user=req.user,
            )
            span.set_attribute("num_chunks", len(chunks))
            retrieval_timer.record_chunks(chunks)

    except Exception as e:
        logger.error("RAG query failed: %s", e)
        rag_queries_total.labels(status="error").inc()
        return QueryResponse(answer="", sources=[], trace_id="")

    sources = _format_sources(chunks)

    # LLM 生成答案
    gen = AnswerGenerator()
    with GenerationTimer(), tracer.start_as_current_span("answer_generation") as span:
        span.set_attribute("num_chunks", len(chunks))
        span.set_attribute("query", req.query)
        result = gen.generate(
            query=req.query,
            chunks=chunks,
            system_prompt=req.system_prompt or settings.rag.default_system_prompt or None,
            temperature=req.temperature,
        )
        span.set_attribute("num_citations", len(result.citations))
        span.set_attribute("answer_length", len(result.answer_text))

    citations = [
        {
            "chunk_id": c.chunk_id,
            "source_doc": c.source_doc,
            "page_numbers": c.page_numbers,
            "content_snippet": c.content_snippet,
        }
        for c in result.citations
    ]

    rag_queries_total.labels(status="success").inc()
    elapsed = time.time() - t_start
    logger.info("RAG query completed in %.2fms: %d chunks, %d citations", elapsed * 1000, len(chunks), len(citations))

    # Fire-and-forget audit logging
    try:
        from super_agent.knowledge.audit import AuditLogger

        audit = AuditLogger()
        await audit.log_query(
            user_id=req.user.user_id,
            query=req.query,
            num_chunks=len(chunks),
            chunk_ids=[c.id for c in chunks],
            answer=result.answer_text,
            num_citations=len(citations),
            latency_ms=elapsed * 1000,
        )
    except Exception:
        logger.warning("Audit log skipped", exc_info=True)

    return QueryResponse(answer=result.answer_text, sources=sources, citations=citations)


@app.post("/rag/query/stream")
async def rag_query_stream(req: QueryRequest):
    """SSE 流式 RAG 检索 + 答案生成。

    与 /rag/query 功能相同，但 LLM 生成部分以 SSE 流式输出。
    前端使用 EventSource 或 fetch + ReadableStream 消费。

    SSE 事件格式：
      data: {"type": "sources", "sources": [...]}   — 检索到的源文档
      data: {"type": "token", "text": "..."}         — LLM 生成的 token
      data: {"type": "citations", "citations": [...]} — 最终引用列表
      data: {"type": "done"}                         — 完成信号
    """
    async def _generate() -> AsyncGenerator[str, None]:
        try:
            retriever = _build_retriever(req.user)

            retrieval_timer = RetrievalTimer()
            with retrieval_timer:
                chunks = _retrieve_chunks(
                    query=req.query, top_k=req.top_k, retriever=retriever,
                    filters=req.filters, user=req.user,
                )
                retrieval_timer.record_chunks(chunks)

            gen = AnswerGenerator()
            for event in gen.generate_stream(
                query=req.query,
                chunks=chunks,
                system_prompt=req.system_prompt or settings.rag.default_system_prompt or None,
                temperature=req.temperature,
            ):
                yield event

            rag_queries_total.labels(status="success").inc()
        except Exception as e:
            logger.error("Stream RAG query failed: %s", e)
            rag_queries_total.labels(status="error").inc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


@app.post("/rag/batch-query", response_model=BatchQueryResponse)
async def rag_batch_query(req: BatchQueryRequest):
    """批量 RAG 检索 + 答案生成。

    同时执行多个 query 的检索和生成，适合一次问多个问题、或分块文档场景。
    内部使用 asyncio.gather 并行执行，减少总等待时间。

    参数（BatchQueryRequest body）:
        queries: list[BatchQueryItem]  — 多个查询，每项含 query / top_k / filters / system_prompt / temperature

    返回:
        results: list[QueryResponse]   — 结果列表，与 queries 顺序一致
    """
    from super_agent.knowledge.retriever import Retriever
    from super_agent.knowledge.embedders import get_embedder
    from super_agent.knowledge.stores import get_store

    embedder = get_embedder()

    es_client = None
    if settings.rag.enable_bm25_hybrid:
        from super_agent.knowledge.es_client import ESClient
        es_client = ESClient()

    store = get_store()

    async def _run_single(item: BatchQueryItem) -> QueryResponse:
        try:
            retriever = Retriever(store=store, embedder=embedder, es_client=es_client)

            chunks = _retrieve_chunks(
                query=item.query, top_k=item.top_k, retriever=retriever,
                filters=item.filters,
            )

            gen = AnswerGenerator()
            result = gen.generate(
                query=item.query,
                chunks=chunks,
                system_prompt=item.system_prompt or settings.rag.default_system_prompt or None,
                temperature=item.temperature,
            )

            sources = _format_sources(chunks)
            citations = [
                {"chunk_id": c.chunk_id, "source_doc": c.source_doc, "page_numbers": c.page_numbers, "content_snippet": c.content_snippet}
                for c in result.citations
            ]
            return QueryResponse(answer=result.answer_text, sources=sources, citations=citations)
        except Exception as e:
            logger.error("Batch sub-query failed: %s", e)
            return QueryResponse(answer="", sources=[])

    results = await asyncio.gather(*[_run_single(item) for item in req.queries])
    return BatchQueryResponse(results=list(results))


@app.post("/rag/index")
async def rag_index(doc_dir: str = "data/raw_docs", force: bool = False, tenant_id: str = "", use_llm: bool = False):
    """构建 / 重建知识库索引。

    加载 doc_dir 下的文档 → 解析 → 语义切分 → Embed → 写入向量库。
    支持增量索引（通过 MD5 文件哈希跳过未变更文件）和全量重建。

    参数:
        doc_dir: str (默认 "data/raw_docs")  — 文档目录路径
        force: bool (默认 False)              — True = 全量重建（清空 + 重新索引）
                                                 False = 增量索引（跳过哈希未变的文件）
        tenant_id: str (默认 "")              — 租户标识，指定写入哪个集合
                                                 空 = 默认集合 super_agent_docs
                                                 "finance" = 集合 super_agent_docs_finance
        use_llm: bool (默认 False)            — True = 使用 LLM 辅助语义切分
                                                 False = 规则切分（标题链 + 句子级 overlap）

    返回:
        status: str          — "indexed" 或 "rebuilt"
        doc_dir: str         — 文档目录路径
        total_chunks: int    — 索引完成后向量库中的总文档块数
    """
    from super_agent.knowledge.indexer import Indexer
    from super_agent.knowledge.stores import get_store
    from super_agent.knowledge.embedders import get_embedder
    from super_agent.knowledge.chunkers import SemanticChunker

    if use_llm:
        from super_agent.knowledge.chunkers.llm_assisted import LLMAssistedChunker
        chunker = LLMAssistedChunker(use_llm=True)
    else:
        chunker = SemanticChunker()

    store = get_store(tenant_id=tenant_id)
    embedder = get_embedder()

    # ES BM25 hybrid client
    es_client = None
    if settings.rag.enable_bm25_hybrid:
        from super_agent.knowledge.es_client import ESClient
        es_client = ESClient()

    indexer = Indexer(store=store, embedder=embedder, chunker=chunker, tenant_id=tenant_id, es_client=es_client)
    if force:
        indexer.rebuild(doc_dir)
    else:
        indexer.build(doc_dir)
    return {"status": "indexed" if not force else "rebuilt", "doc_dir": doc_dir, "total_chunks": store.count()}


@app.post("/rag/delete", response_model=DeleteResponse)
async def rag_delete(req: DeleteRequest):
    """删除或清空向量库中的文档块。

    参数（DeleteRequest body）:
        chunk_ids: list[str] | None  — 指定要删除的 chunk ID 列表
                                       null = 清空整个集合
        tenant_id: str (默认 "")      — 租户标识，操作哪个集合

    返回:
        status: str          — "ok" 或 "error"
        deleted_count: int   — 实际删除的文档块数量
    """
    try:
        from super_agent.knowledge.stores import get_store

        store = get_store(tenant_id=req.tenant_id)
        prev_count = store.count()

        if req.chunk_ids:
            store.delete(req.chunk_ids)
        else:
            store.clear()

        new_count = store.count()
        return DeleteResponse(status="ok", deleted_count=prev_count - new_count)
    except Exception as e:
        logger.error("RAG delete failed: %s", e)
        return DeleteResponse(status="error", deleted_count=0)


@app.post("/rag/doc/status")
async def rag_doc_status(doc_path: str, tenant_id: str = ""):
    """查询指定文档的索引状态（版本、哈希、最近索引时间）。

    参数:
        doc_path: str          — 文档文件路径（与索引时一致）
        tenant_id: str (默认 "") — 租户标识

    返回:
        status: str        — "found" 或 "not_found"
        file_path: str     — 文档路径（仅 found 时返回）
        version: str       — 文档版本号（仅 found 时返回）
        file_hash: str     — MD5 文件哈希（仅 found 时返回）
        last_indexed: str  — 最近索引时间 ISO 格式（仅 found 时返回）
    """
    from super_agent.knowledge.stores import get_store
    from super_agent.knowledge.embedders import get_embedder
    from super_agent.knowledge.chunkers import SemanticChunker

    store = get_store(tenant_id=tenant_id)
    embedder = get_embedder()
    chunker = SemanticChunker()
    indexer = Indexer(store=store, embedder=embedder, chunker=chunker, tenant_id=tenant_id)
    status = indexer.get_document_status(doc_path)
    if status is None:
        return {"status": "not_found", "file_path": doc_path}
    return {"status": "found", **status}


@app.post("/rag/doc/list")
async def rag_doc_list(tenant_id: str = ""):
    """列出指定租户下所有已索引的文档及其版本信息。

    参数:
        tenant_id: str (默认 "") — 租户标识

    返回:
        documents: list[dict] — 文档列表，每项含 file_path / version / last_indexed
    """
    from super_agent.knowledge.stores import get_store
    from super_agent.knowledge.embedders import get_embedder
    from super_agent.knowledge.chunkers import SemanticChunker

    store = get_store(tenant_id=tenant_id)
    embedder = get_embedder()
    chunker = SemanticChunker()
    indexer = Indexer(store=store, embedder=embedder, chunker=chunker, tenant_id=tenant_id)
    return {"documents": indexer.list_documents()}


def main():
    import uvicorn
    uvicorn.run(
        "super_agent.main:app",
        host=settings.server.host,
        port=settings.server.port,
        workers=settings.server.workers,
        reload=settings.env == "dev",
    )


if __name__ == "__main__":
    main()
