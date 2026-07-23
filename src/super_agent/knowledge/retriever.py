from __future__ import annotations

from typing import Any

from langsmith import traceable

from super_agent.knowledge.models import Chunk, SearchResult, UserContext
from super_agent.knowledge.stores.base import BaseVectorStore
from super_agent.knowledge.embedders.base import BaseEmbedder
from super_agent.knowledge.bm25 import BM25Search

LEVEL_ORDER = {"L1": 1, "L2": 2, "L3": 3}


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
        merged_filters = self._build_filters(filters, user)
        query_emb = self.embedder.embed_query(query)

        # 1. 向量检索（始终执行）
        vector_results = self.store.search(query_emb, top_k * 3, merged_filters)
        search_sets = [vector_results]

        # 2. ES BM25 检索（可选）
        if self.es_client:
            es_matches = self.es_client.search(query, top_k * 3)
            if es_matches:
                # 将 ES (chunk_id, score) 转为 SearchResult
                vector_map = {r.chunk.id: r.chunk for r in vector_results}
                es_results = []
                for cid, score in es_matches:
                    chunk = vector_map.get(cid)
                    if chunk is None:
                        # ES 独有命中：创建最小 chunk（内容后续回填）
                        chunk = Chunk(id=cid, content="", heading_chain="", full_text="", metadata={})
                    es_results.append(SearchResult(chunk=chunk, score=score))
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

        # 7. 回填 ES 独有 chunk 的内容（从向量库按 ID 捞回）
        empty_ids = [r.chunk.id for r in candidates if not r.chunk.content]
        if empty_ids:
            hydrated = self.store.search(query_emb, top_k * 5, merged_filters)
            hydrated_map = {r.chunk.id: r.chunk for r in hydrated}
            for r in candidates:
                if not r.chunk.content and r.chunk.id in hydrated_map:
                    r.chunk = hydrated_map[r.chunk.id]

        return [r.chunk for r in candidates[:top_k]]

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
        es_client=None,
        reranker: Any | None = None,
    ):
        self.stores = stores
        self.embedder = embedder
        self.es_client = es_client
        self.reranker = reranker

    @traceable(name="multi_store_retriever.retrieve", run_type="chain")
    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
        user: UserContext | None = None,
    ) -> list[Chunk]:
        merged_filters = self._build_filters(filters, user)
        query_emb = self.embedder.embed_query(query)

        # 1. 检索每个向量库，然后用 RRF 合并为一个向量排名
        store_results: list[list[SearchResult]] = []
        for store in self.stores:
            vector_results = store.search(query_emb, top_k * 3, merged_filters)
            store_results.append([SearchResult(chunk=c, score=1.0) for c in vector_results])

        if len(store_results) > 1:
            vector_ranked = reciprocal_rank_fusion(*store_results, k=60)
        else:
            vector_ranked = store_results[0] if store_results else []

        search_sets: list[list[SearchResult]] = [vector_ranked]

        # 2. ES BM25 检索（第二种检索方式）
        if self.es_client:
            es_matches = self.es_client.search(query, top_k * 3)
            if es_matches:
                vector_map: dict[str, Chunk] = {sr.chunk.id: sr.chunk for sr in vector_ranked}
                es_results = []
                for cid, score in es_matches:
                    chunk = vector_map.get(cid)
                    if chunk is None:
                        chunk = Chunk(id=cid, content="", heading_chain="", full_text="", metadata={})
                    es_results.append(SearchResult(chunk=chunk, score=score))
                search_sets.append(es_results)

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

        # 6. 回填 ES 独有 chunk 的内容
        empty_ids = [r.chunk.id for r in candidates if not r.chunk.content]
        if empty_ids:
            hydrated = self.stores[0].search(query_emb, top_k * 5, merged_filters)
            hydrated_map = {c.id: c for c in hydrated}
            for r in candidates:
                if not r.chunk.content and r.chunk.id in hydrated_map:
                    r.chunk = hydrated_map[r.chunk.id]

        return [r.chunk for r in candidates[:top_k]]

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
