from __future__ import annotations

import logging
from typing import Any

from langsmith import traceable

from super_agent.knowledge.bm25 import BM25Search
from super_agent.knowledge.embedders.base import BaseEmbedder
from super_agent.knowledge.models import Chunk, SearchResult, UserContext
from super_agent.knowledge.stores.base import BaseVectorStore

logger = logging.getLogger(__name__)

LEVEL_ORDER = {"L1": 1, "L2": 2, "L3": 3}


def _log_search_results(channel: str, results: list[SearchResult], top_n: int = 5) -> None:
    """记录检索结果摘要（每条：分数 | 来源 | 内容预览）。"""
    if not results:
        logger.info("  %s: 0 results", channel)
        return
    logger.info("  %s: %d results", channel, len(results))
    for i, r in enumerate(results[:top_n]):
        fpath = r.chunk.metadata.get("file_path", "") if hasattr(r.chunk, "metadata") else ""
        preview = (r.chunk.content or "")[:120].replace("\n", " ")
        logger.info("    [%d] score=%.4f  source=%s  preview=%s", i, r.score, fpath, preview)


def allowed_levels(user_level: str) -> list[str]:
    """用户能看的密级列表：level=N 可看所有 <=N 的级别。"""
    max_lv = LEVEL_ORDER.get(user_level, 2)
    return [lv for lv, order in LEVEL_ORDER.items() if order <= max_lv]


class Retriever:
    def __init__(
        self,
        store: BaseVectorStore,
        embedder: BaseEmbedder,
        bm25: BM25Search | None = None,
        reranker: Any | None = None,  # RemoteReranker 实例，None = 跳过精排
        use_hybrid: bool = False,
        es_client=None,  # ESClient | None: ES BM25 混合检索
    ):
        self.store = store
        self.embedder = embedder
        self.bm25 = bm25
        self.reranker = reranker
        self.use_hybrid = use_hybrid and bm25 is not None
        self.es_client = es_client

    @traceable(name="retriever.retrieve", run_type="chain")
    def retrieve(self, query: str, top_k: int = 5, filters: dict | None = None, user: UserContext | None = None) -> list[Chunk]:
        store_name = getattr(self.store, "collection_name", type(self.store).__name__)
        es_index = self.es_client.index_name if self.es_client else "N/A"
        logger.info("检索: query=%s vector_store=%s es_index=%s", query[:80], store_name, es_index)
        merged_filters = self._build_filters(filters, user)
        query_emb = self.embedder.embed_query(query)

        # 1. 向量检索（始终执行）
        vector_results = self.store.search(query_emb, top_k * 3, merged_filters)
        _log_search_results(f"向量库[{store_name}]", vector_results)
        search_sets = [vector_results]

        # 2. ES BM25 检索（可选）
        if self.es_client:
            es_matches = self.es_client.search(query, top_k * 3)
            if es_matches:
                vector_map = {r.chunk.id: r.chunk for r in vector_results}
                es_results = []
                for cid, score, source in es_matches:
                    chunk = vector_map.get(cid)
                    if chunk is None:
                        # ES 独有命中 → 从 ES source 构建 Chunk
                        chunk = Chunk(
                            id=cid,
                            content=source.get("content", ""),
                            heading_chain=source.get("heading_chain", ""),
                            full_text=source.get("content", ""),
                            metadata={
                                "file_path": source.get("file_path", ""),
                                "doc_type": source.get("doc_type", ""),
                                "department": source.get("department", ""),
                                "topic_tags": source.get("topic_tags", []),
                                "chunk_type": source.get("chunk_type", "text"),
                                "page_numbers": source.get("page_numbers", []),
                                "doc_version": source.get("doc_version", ""),
                            },
                        )
                    es_results.append(SearchResult(chunk=chunk, score=score))
                _log_search_results(f"ES BM25[{es_index}]", es_results)
                search_sets.append(es_results)

        # 3. 本地 BM25（旧版，可选）
        if self.use_hybrid and self.bm25:
            bm25_results = self.bm25.search(query, top_k * 3)
            search_sets.append(bm25_results)

        # 4. RRF 融合：将多种检索结果按排名加权合并
        if len(search_sets) > 1:
            candidates = reciprocal_rank_fusion(*search_sets, k=60)
        else:
            candidates = vector_results

        # 5. Rerank 精排（可选）
        if self.reranker:
            candidates = self.reranker.rerank(query, candidates, top_k)

        # 6. 去重：移除重叠 chunk，保留分数最高的版本
        candidates = deduplicate_overlaps(candidates)

        # 7. 过滤空内容 chunk（ES 脏数据等导致向量库中已不存在）
        candidates = [r for r in candidates if r.chunk.content.strip()]

        result = [r.chunk for r in candidates[:top_k]]
        _log_search_results("最终结果", candidates[:top_k])
        return result

    def _build_filters(self, user_filters: dict | None, user: UserContext | None) -> dict | None:
        """合并用户传入的过滤条件与自动注入的权限过滤条件。"""
        result: dict = {}

        # 文档状态：始终排除已过期/已停用的文档
        result["doc_status"] = {"$eq": "active"}

        # 文档密级：按用户权限过滤
        # L3 → L1+L2+L3, L2 → L1+L2, L1 → L1
        if user and user.doc_level:
            result["doc_level"] = {"$in": allowed_levels(user.doc_level)}

        # 合并用户自定义过滤条件（AND 逻辑）
        if user_filters:
            for key, value in user_filters.items():
                result[key] = value

        return result if result else None


class MultiStoreRetriever:
    """检索多个向量存储，通过 RRF 融合后返回，可选 ES BM25 + Reranker。"""

    def __init__(
        self,
        stores: list[BaseVectorStore],
        embedder: BaseEmbedder,
        es_clients: list | None = None,
        reranker: Any | None = None,
    ):
        self.stores = stores
        self.embedder = embedder
        self.es_clients = es_clients or []
        self.reranker = reranker

    @traceable(name="multi_store_retriever.retrieve", run_type="chain")
    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
        user: UserContext | None = None,
    ) -> list[Chunk]:
        store_names = [getattr(s, "collection_name", type(s).__name__) for s in self.stores]
        es_indices = [c.index_name for c in self.es_clients] or ["N/A"]
        logger.info("检索: query=%s vector_stores=%s es_indices=%s", query[:80], store_names, es_indices)
        merged_filters = self._build_filters(filters, user)
        query_emb = self.embedder.embed_query(query)

        # 1. 检索每个向量库，然后用 RRF 合并为一个向量排名
        store_results: list[list[SearchResult]] = []
        for store in self.stores:
            vector_results = store.search(query_emb, top_k * 3, merged_filters)
            sname = getattr(store, "collection_name", type(store).__name__)
            _log_search_results(f"向量库[{sname}]", vector_results)
            store_results.append(vector_results)

        if len(store_results) > 1:
            vector_ranked = reciprocal_rank_fusion(*store_results, k=60)
            _log_search_results("向量库[RRF融合]", vector_ranked)
        else:
            vector_ranked = store_results[0] if store_results else []

        search_sets: list[list[SearchResult]] = [vector_ranked]
        vector_map: dict[str, Chunk] = {sr.chunk.id: sr.chunk for sr in vector_ranked}

        # 2. ES BM25 检索（每个索引独立检索后 RRF 融合）
        if self.es_clients:
            es_all_results: list[list[SearchResult]] = []
            for es_client in self.es_clients:
                es_matches = es_client.search(query, top_k * 3)
                if es_matches:
                    es_results = []
                    for cid, score, source in es_matches:
                        chunk = vector_map.get(cid)
                        if chunk is None:
                            # ES 独有命中 → 从 ES source 构建 Chunk
                            chunk = Chunk(
                                id=cid,
                                content=source.get("content", ""),
                                heading_chain=source.get("heading_chain", ""),
                                full_text=source.get("content", ""),
                                metadata={
                                    "file_path": source.get("file_path", ""),
                                    "doc_type": source.get("doc_type", ""),
                                    "department": source.get("department", ""),
                                    "topic_tags": source.get("topic_tags", []),
                                    "chunk_type": source.get("chunk_type", "text"),
                                    "page_numbers": source.get("page_numbers", []),
                                    "doc_version": source.get("doc_version", ""),
                                },
                            )
                        es_results.append(SearchResult(chunk=chunk, score=score))
                    _log_search_results(f"ES BM25[{es_client.index_name}]", es_results)
                    es_all_results.append(es_results)

            if es_all_results:
                # 多个索引的 ES 结果先 RRF 融合为一个排名列表
                if len(es_all_results) > 1:
                    fused_es = reciprocal_rank_fusion(*es_all_results, k=60)
                    search_sets.append(fused_es)
                else:
                    search_sets.append(es_all_results[0])

        # 3. RRF 融合：向量排名 + BM25 排名（两种不同的检索方式）
        if len(search_sets) > 1:
            candidates = reciprocal_rank_fusion(*search_sets, k=60)
        else:
            candidates = search_sets[0]

        # 4. Rerank 精排（可选）
        if self.reranker:
            candidates = self.reranker.rerank(query, candidates, top_k)

        # 5. 去重
        candidates = deduplicate_overlaps(candidates)

        # 6. 过滤空内容 chunk（ES 脏数据等导致向量库中已不存在）
        candidates = [r for r in candidates if r.chunk.content.strip()]

        result = [r.chunk for r in candidates[:top_k]]
        _log_search_results("最终结果", candidates[:top_k])
        return result

    def _build_filters(self, user_filters: dict | None, user: UserContext | None) -> dict | None:
        """与 Retriever._build_filters 相同的过滤逻辑。"""
        result: dict = {}
        result["doc_status"] = {"$eq": "active"}
        if user and user.doc_level:
            result["doc_level"] = {"$in": allowed_levels(user.doc_level)}
        if user_filters:
            for key, value in user_filters.items():
                result[key] = value
        return result if result else None


def reciprocal_rank_fusion(
    *result_sets: list[SearchResult], k: int = 60
) -> list[SearchResult]:
    """RRF（Reciprocal Rank Fusion）：将多个排序结果按排名加权合并。

    核心公式：score(c) = Σ 1 / (k + rank(c) + 1)
    即文档在某个结果集中的排名越靠前，贡献的分数越高。
    默认 k=60 抑制极端排名的影响。
    """
    scores: dict[str, float] = {}
    chunk_map: dict[str, SearchResult] = {}

    for results in result_sets:
        for rank, r in enumerate(results):
            scores[r.chunk.id] = scores.get(r.chunk.id, 0.0) + 1.0 / (k + rank + 1)
            if r.chunk.id not in chunk_map:
                chunk_map[r.chunk.id] = r

    sorted_ids = sorted(scores, key=scores.get, reverse=True)
    merged = []
    for cid in sorted_ids:
        r = chunk_map[cid]
        r.score = scores[cid]
        merged.append(r)
    return merged


def deduplicate_overlaps(results: list[SearchResult]) -> list[SearchResult]:
    """去除重叠 chunk，保留每个源 chunk 中分数最高的版本。"""
    seen_source: dict[str, SearchResult] = {}
    for r in results:
        source_id = r.chunk.overlap_source_chunk_id or r.chunk.id
        if source_id not in seen_source or r.score > seen_source[source_id].score:
            seen_source[source_id] = r
    return sorted(seen_source.values(), key=lambda x: x.score, reverse=True)
