from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from pydantic import BaseModel, Field

from super_agent.config import settings
from super_agent.knowledge.indexer import Indexer
from super_agent.knowledge.models import UserContext
from super_agent.tracing.metrics import (  # Prometheus 监控指标，暴露 /metrics 端点，无基础设施时不影响运行
    CONTENT_TYPE_LATEST,
    GenerationTimer,
    RetrievalTimer,
    generate_latest,
    rag_queries_total,
)

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


class DeleteResponse(BaseModel):
    status: str
    deleted_count: int = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=getattr(logging, settings.server.log_level))
    logger.info("Super Agent starting in %s mode", settings.env)
    yield


app = FastAPI(
    title="Super Agent",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/rag/query", response_model=QueryResponse)
async def rag_query(req: QueryRequest):
    t_start = time.time()
    try:
        from super_agent.knowledge.retriever import Retriever
        from super_agent.knowledge.stores import get_store
        from super_agent.knowledge.embedders import get_embedder
        from super_agent.knowledge.generator import AnswerGenerator
        from super_agent.knowledge.query_processor import QueryProcessor

        store = get_store()
        embedder = get_embedder()
        retriever = Retriever(store=store, embedder=embedder)

        # Query understanding: rewrite before retrieval
        qp = QueryProcessor()
        processed = qp.process(req.query)
        search_query = processed.rewritten

        retrieval_timer = RetrievalTimer()
        with retrieval_timer:
            chunks = retriever.retrieve(search_query, top_k=req.top_k, filters=req.filters, user=req.user)
        retrieval_timer.record_chunks(chunks)

    except Exception as e:
        logger.error("RAG query failed: %s", e)
        rag_queries_total.labels(status="error").inc()
        return QueryResponse(
            answer="",
            sources=[],
            trace_id="",
        )

    sources = [
        {
            "chunk_id": c.id,
            "content": c.content[:200],
            "metadata": c.metadata,
            "page_numbers": c.page_numbers,
        }
        for c in chunks
    ]

    gen = AnswerGenerator()
    with GenerationTimer():
        result = gen.generate(
            query=req.query,
            chunks=chunks,
            system_prompt=req.system_prompt or settings.rag.default_system_prompt or None,
            temperature=req.temperature,
        )

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


@app.post("/rag/index")
async def rag_index(doc_dir: str = "data/raw_docs", force: bool = False):
    from super_agent.knowledge.indexer import Indexer
    from super_agent.knowledge.stores import get_store
    from super_agent.knowledge.embedders import get_embedder
    from super_agent.knowledge.chunkers import SemanticChunker

    store = get_store()
    embedder = get_embedder()
    chunker = SemanticChunker()
    indexer = Indexer(store=store, embedder=embedder, chunker=chunker)
    if force:
        indexer.rebuild(doc_dir)
    else:
        indexer.build(doc_dir)
    return {"status": "indexed" if not force else "rebuilt", "doc_dir": doc_dir, "total_chunks": store.count()}


@app.post("/rag/delete", response_model=DeleteResponse)
async def rag_delete(req: DeleteRequest):
    try:
        from super_agent.knowledge.stores import get_store

        store = get_store()
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
async def rag_doc_status(doc_path: str):
    from super_agent.knowledge.stores import get_store
    from super_agent.knowledge.embedders import get_embedder
    from super_agent.knowledge.chunkers import SemanticChunker

    store = get_store()
    embedder = get_embedder()
    chunker = SemanticChunker()
    indexer = Indexer(store=store, embedder=embedder, chunker=chunker)
    status = indexer.get_document_status(doc_path)
    if status is None:
        return {"status": "not_found", "file_path": doc_path}
    return {"status": "found", **status}


@app.post("/rag/doc/list")
async def rag_doc_list():
    from super_agent.knowledge.stores import get_store
    from super_agent.knowledge.embedders import get_embedder
    from super_agent.knowledge.chunkers import SemanticChunker

    store = get_store()
    embedder = get_embedder()
    chunker = SemanticChunker()
    indexer = Indexer(store=store, embedder=embedder, chunker=chunker)
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
