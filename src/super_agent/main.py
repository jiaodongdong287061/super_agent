from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import iterate_in_threadpool

from super_agent.config import settings
from super_agent.knowledge.indexer import Indexer
from super_agent.knowledge.models import UserContext
from super_agent.knowledge.generator import AnswerGenerator
from super_agent.knowledge.retrieval_pipeline import build_retriever, retrieve_chunks
from super_agent.tracing.metrics import (
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
    department: str = ""


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
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    setup_tracing()

    # 初始化 Agent Runtime（Phase 2）
    # AgentRuntime 直接导入 retrieval_pipeline，不再需要注入 rag_fn
    from super_agent.core import AgentRuntime, Guardrails, HumanApprovalGateway
    from super_agent.api import agent as agent_api

    guardrails = Guardrails()
    hitl = HumanApprovalGateway()

    rt = AgentRuntime(guardrails=guardrails, hitl=hitl)
    agent_api.init_runtime(rt, hitl)

    logger.info("Super Agent starting in %s mode", settings.env)
    yield


app = FastAPI(
    title="Super Agent",
    version="0.1.0",
    lifespan=lifespan,
)

# OAuth2 SSO 认证
from super_agent.api.sso import SSOMiddleware, register_sso_routes
app.add_middleware(SSOMiddleware)
register_sso_routes(app)

# Agent 路由
from super_agent.api import agent as agent_api
app.include_router(agent_api.router)

# ── Core helpers ──────────────────────────────────────────────


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


def _resolve_user(request: Request, body_user: UserContext) -> UserContext:
    state_user = getattr(request, "state", None)
    if state_user and hasattr(state_user, "user"):
        user = state_user.user
        logger.info("Resolved user from SSO: user_id=%s department=%s roles=%s",
                     user.user_id, user.department, user.roles)
        return user
    logger.info("Resolved user from request body: user_id=%s department=%s roles=%s",
                 body_user.user_id, body_user.department, body_user.roles)
    return body_user


# ── Endpoints (sync, FastAPI auto-runs sync def in thread pool) ─────


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/rag/query", response_model=QueryResponse)
async def rag_query(req: QueryRequest, request: Request):
    logger.info("POST /rag/query query=%s top_k=%s filters=%s",
                req.query[:100], req.top_k, req.filters)
    t_start = time.time()
    try:
        user = _resolve_user(request, req.user)
        # to_thread: 将同步操作移出事件循环线程，防止阻塞 FastAPI 并发处理其他请求
        retriever = await asyncio.to_thread(build_retriever, user)

        retrieval_timer = RetrievalTimer()
        with retrieval_timer, tracer.start_as_current_span("retrieval") as span:
            chunks = await asyncio.to_thread(
                retrieve_chunks, req.query, req.top_k, retriever,
                req.filters, user,
            )
            span.set_attribute("num_chunks", len(chunks))
            retrieval_timer.record_chunks(chunks)

        if chunks:
            logger.info("RAG retrieved %d chunks for query: %s", len(chunks), req.query[:60])
            for i, c in enumerate(chunks[:3]):
                logger.info("RAG chunk[%d]: source=%s content_preview=%s", i,
                            c.metadata.get("file_path", "") if hasattr(c, "metadata") else "",
                            (c.content[:200] if hasattr(c, "content") else str(c))[:200])
        else:
            logger.info("RAG returned 0 chunks for query: %s", req.query[:60])

    except Exception as e:
        logger.error("RAG query failed: %s", e)
        rag_queries_total.labels(status="error").inc()
        return QueryResponse(answer="", sources=[], trace_id="")

    sources = _format_sources(chunks)

    gen = AnswerGenerator()
    with GenerationTimer(), tracer.start_as_current_span("answer_generation") as span:
        span.set_attribute("num_chunks", len(chunks))
        span.set_attribute("query", req.query)
        result = await asyncio.to_thread(
            gen.generate,
            query=req.query, chunks=chunks,
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

    # 审计日志（异步写入，不阻塞返回）
    try:
        from super_agent.knowledge.audit import AuditLogger

        audit = AuditLogger()
        await audit.log_query(
            user_id=user.user_id, query=req.query,
            num_chunks=len(chunks), chunk_ids=[c.id for c in chunks],
            answer=result.answer_text, num_citations=len(citations),
            latency_ms=elapsed * 1000,
        )
    except Exception:
        logger.warning("Audit log skipped", exc_info=True)

    return QueryResponse(answer=result.answer_text, sources=sources, citations=citations)


@app.post("/rag/query/stream")
async def rag_query_stream(req: QueryRequest, request: Request):
    logger.info("POST /rag/query/stream query=%s top_k=%s", req.query[:100], req.top_k)

    async def _generate():
        try:
            user = _resolve_user(request, req.user)
            # to_thread: 将同步操作移出事件循环线程，防止阻塞 FastAPI 并发处理其他请求
            retriever = await asyncio.to_thread(build_retriever, user)

            retrieval_timer = RetrievalTimer()
            with retrieval_timer:
                chunks = await asyncio.to_thread(
                    retrieve_chunks, req.query, req.top_k, retriever,
                    req.filters, user,
                )
                retrieval_timer.record_chunks(chunks)

            if chunks:
                logger.info("RAG retrieved %d chunks for query: %s", len(chunks), req.query[:60])
                for i, c in enumerate(chunks[:3]):
                    logger.info("RAG chunk[%d]: source=%s content_preview=%s", i,
                                c.metadata.get("file_path", "") if hasattr(c, "metadata") else "",
                                (c.content[:200] if hasattr(c, "content") else str(c))[:200])
            else:
                logger.info("RAG returned 0 chunks for query: %s", req.query[:60])

            gen = AnswerGenerator()
            # iterate_in_threadpool: 将同步生成器放入线程池运行，逐次 yield 不阻塞事件循环
            async for event in iterate_in_threadpool(
                gen.generate_stream(
                    query=req.query,
                    chunks=chunks,
                    system_prompt=req.system_prompt or settings.rag.default_system_prompt or None,
                    temperature=req.temperature,
                )
            ):
                yield event

            rag_queries_total.labels(status="success").inc()
        except Exception as e:
            logger.error("Stream RAG query failed: %s", e)
            rag_queries_total.labels(status="error").inc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


@app.post("/rag/batch-query", response_model=BatchQueryResponse)
def rag_batch_query(req: BatchQueryRequest):
    logger.info("POST /rag/batch-query queries=%d", len(req.queries))
    from super_agent.knowledge.retriever import Retriever
    from super_agent.knowledge.embedders import get_embedder
    from super_agent.knowledge.stores import get_store

    embedder = get_embedder()

    es_client = None
    if settings.rag.enable_bm25_hybrid:
        try:
            from super_agent.knowledge.es_client import ESClient
            es_client = ESClient()
        except Exception as e:
            logger.warning("ES client init failed, BM25 hybrid disabled: %s", e)

    store = get_store()

    def _run_single(item: BatchQueryItem) -> QueryResponse:
        try:
            retriever = Retriever(store=store, embedder=embedder, es_client=es_client)

            chunks = retrieve_chunks(
                query=item.query, top_k=item.top_k, retriever=retriever,
                filters=item.filters,
            )

            gen = AnswerGenerator()
            result = gen.generate(
                query=item.query, chunks=chunks,
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

    results = [_run_single(item) for item in req.queries]
    return BatchQueryResponse(results=list(results))


@app.post("/rag/index")
async def rag_index(doc_dir: str = "data/raw_docs", force: bool = False, tenant_id: str = "",
                     use_llm: bool = False, department: str = "", doc_level: str = "L1"):
    logger.info("POST /rag/index doc_dir=%s force=%s tenant_id=%s department=%s doc_level=%s",
                doc_dir, force, tenant_id, department, doc_level)
    from super_agent.knowledge.indexer import Indexer
    from super_agent.knowledge.stores import get_store
    from super_agent.knowledge.embedders import get_embedder
    from super_agent.knowledge.chunkers import SemanticChunker

    embedder = get_embedder()

    if use_llm:
        from super_agent.knowledge.chunkers.llm_assisted import LLMAssistedChunker
        chunker = LLMAssistedChunker(use_llm=True)
    else:
        chunker = SemanticChunker(embedder=embedder)

    effective_tenant = department or tenant_id
    store = get_store(tenant_id=effective_tenant)

    es_client = None
    if settings.rag.enable_bm25_hybrid:
        try:
            from super_agent.knowledge.es_client import ESClient
            es_client = ESClient(tenant_id=effective_tenant)
        except Exception as e:
            logger.warning("ES client init failed, BM25 hybrid disabled: %s", e)

    indexer = Indexer(
        store=store, embedder=embedder, chunker=chunker,
        tenant_id=effective_tenant, es_client=es_client,
        doc_level=doc_level,
    )
    if force:
        await asyncio.to_thread(indexer.rebuild, doc_dir)
    else:
        await asyncio.to_thread(indexer.build, doc_dir)
    total_chunks = await asyncio.to_thread(store.count)
    return {"status": "indexed" if not force else "rebuilt", "doc_dir": doc_dir, "total_chunks": total_chunks}


@app.post("/rag/delete", response_model=DeleteResponse)
def rag_delete(req: DeleteRequest):
    logger.info("POST /rag/delete chunk_ids=%s tenant_id=%s department=%s",
                len(req.chunk_ids) if req.chunk_ids else None, req.tenant_id, req.department)
    try:
        from super_agent.knowledge.stores import get_store

        effective_tenant = req.department or req.tenant_id
        store = get_store(tenant_id=effective_tenant)
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


@app.post("/rag/clear")
def rag_clear(tenant_id: str = "", department: str = ""):
    """清空指定租户/部门的向量集合和 ES 索引；不传则清空公共库。"""
    effective_tenant = department or tenant_id
    label = effective_tenant or "public"
    logger.info("POST /rag/clear tenant=%s", label)

    from super_agent.knowledge.stores import get_store

    # 清空向量库
    store = get_store(tenant_id=effective_tenant)
    store.clear()

    # 清空 ES 索引
    if settings.rag.enable_bm25_hybrid:
        try:
            from super_agent.knowledge.es_client import ESClient
            es = ESClient(tenant_id=effective_tenant)
            es.ensure_index()
            es.clear()
        except Exception as e:
            logger.warning("ES clear failed (non-blocking): %s", e)

    return {"status": "ok", "tenant": label}


@app.post("/rag/doc/status")
def rag_doc_status(doc_path: str, tenant_id: str = ""):
    logger.info("POST /rag/doc/status doc_path=%s tenant_id=%s", doc_path, tenant_id)
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
def rag_doc_list(tenant_id: str = ""):
    logger.info("POST /rag/doc/list tenant_id=%s", tenant_id)
    from super_agent.knowledge.stores import get_store
    from super_agent.knowledge.embedders import get_embedder
    from super_agent.knowledge.chunkers import SemanticChunker

    store = get_store(tenant_id=tenant_id)
    embedder = get_embedder()
    chunker = SemanticChunker()
    indexer = Indexer(store=store, embedder=embedder, chunker=chunker, tenant_id=tenant_id)
    return {"documents": indexer.list_documents()}


@app.get("/rag/collections")
def rag_collections():
    logger.info("GET /rag/collections")
    from super_agent.knowledge.stores import list_collections
    return {"collections": list_collections()}


# 静态文件（前端聊天界面），放在所有 API 路由之后以免拦截请求
static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


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
