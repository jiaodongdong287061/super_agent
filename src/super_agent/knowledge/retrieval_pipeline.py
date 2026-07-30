"""
retrieval_pipeline — RAG 检索管线，供 main.py 和 runtime.py 共用。

完整流程：
query → QueryProcessor(改写+扩展) → 向量检索(3×top_k) → BM25(可选) → RRF融合 → 去重 → 返回
"""

from __future__ import annotations

import logging
from typing import Any

from super_agent.config import settings
from super_agent.knowledge.models import UserContext

logger = logging.getLogger(__name__)


def _build_reranker():
    """构建 Reranker 实例（惰性导入，避免不必要的依赖加载）。"""
    if settings.rerank.provider != "remote":
        return None
    from super_agent.knowledge.remote_reranker import RemoteReranker
    return RemoteReranker(
        api_url=settings.rerank.api_url,
        api_key=settings.rerank.api_key,
        top_n=settings.rerank.top_n,
    )


def build_retriever(user: UserContext):
    """
    根据用户上下文构建检索器。

    单人 → Retriever（单租户）
    Admin → MultiStoreRetriever（全租户）
    部门用户 → MultiStoreRetriever（部门 + 公共）
    """
    from super_agent.knowledge.retriever import Retriever, MultiStoreRetriever
    from super_agent.knowledge.stores import get_store, get_all_tenant_stores, discover_tenant_collections
    from super_agent.knowledge.embedders import get_embedder

    embedder = get_embedder()
    reranker = _build_reranker()

    public_store = get_store()

    # 公共 ES 索引
    public_es = None
    if settings.rag.enable_bm25_hybrid:
        try:
            from super_agent.knowledge.es_client import ESClient
            public_es = ESClient()
        except Exception as e:
            logger.warning("ES client init failed, BM25 hybrid disabled: %s", e)

    if not user.department:
        return Retriever(store=public_store, embedder=embedder, reranker=reranker, es_client=public_es)

    if "admin" in user.roles:
        dept_stores = get_all_tenant_stores()
        all_stores = [public_store] + dept_stores

        es_clients = [public_es] if public_es else []
        if public_es:
            base_name = "super_agent_docs"
            for col_name in discover_tenant_collections():
                tenant_id = col_name[len(base_name) + 1:]
                es_clients.append(ESClient(tenant_id=tenant_id))

        return MultiStoreRetriever(stores=all_stores, embedder=embedder,
                                   es_clients=es_clients if es_clients else None,
                                   reranker=reranker)

    dept_store = get_store(tenant_id=user.department)
    dept_es = ESClient(tenant_id=user.department) if settings.rag.enable_bm25_hybrid else None
    es_clients = [c for c in [dept_es, public_es] if c is not None]
    return MultiStoreRetriever(stores=[dept_store, public_store], embedder=embedder,
                               es_clients=es_clients or None, reranker=reranker)


def retrieve_chunks(
    query: str,
    top_k: int,
    retriever,
    filters: dict | None = None,
    user: UserContext | None = None,
) -> list:
    """
    执行 RAG 检索全流程。

    Args:
        query: 用户问题
        top_k: 返回条数
        retriever: 检索器实例（build_retriever 的返回值）
        filters: 元数据过滤条件
        user: 用户上下文

    Returns:
        list[Chunk]: 检索到的文档块
    """
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


def chunks_to_dicts(chunks: list) -> list[dict]:
    """将 Chunk 对象转为 dict，方便序列化和传参。"""
    return [
        {
            "id": c.id,
            "content": c.content,
            "source_doc": c.metadata.get("file_path", "") if hasattr(c, "metadata") else "",
            "page_numbers": c.page_numbers if hasattr(c, "page_numbers") else None,
        }
        for c in chunks
    ]
