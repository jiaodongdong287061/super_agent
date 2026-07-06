from __future__ import annotations

from super_agent.knowledge.models import Chunk, SearchResult, UserContext
from super_agent.knowledge.stores.base import BaseVectorStore
from super_agent.knowledge.embedders.base import BaseEmbedder
from super_agent.knowledge.bm25 import BM25Search
from super_agent.knowledge.reranker import BGEReranker


class Retriever:
    def __init__(
        self,
        store: BaseVectorStore,
        embedder: BaseEmbedder,
        bm25: BM25Search | None = None,
        reranker: BGEReranker | None = None,
        use_hybrid: bool = False,
    ):
        self.store = store
        self.embedder = embedder
        self.bm25 = bm25
        self.reranker = reranker
        self.use_hybrid = use_hybrid and bm25 is not None

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
        user: UserContext | None = None,
    ) -> list[Chunk]:
        merged_filters = self._build_filters(filters, user)
        query_emb = self.embedder.embed_query(query)
        candidates = self.store.search(query_emb, top_k * 3, merged_filters)

        if self.use_hybrid and self.bm25:
            bm25_results = self.bm25.search(query, top_k * 3)
            candidates = self._reciprocal_rank_fusion(candidates, bm25_results)

        if self.reranker:
            candidates = self.reranker.rerank(query, candidates, top_k)

        candidates = self._deduplicate_overlaps(candidates)
        return [r.chunk for r in candidates[:top_k]]

    def _build_filters(self, user_filters: dict | None, user: UserContext | None) -> dict | None:
        """Merge user-supplied filters with auto-injected permission/tenant filters."""
        result: dict = {}

        # Document status: always exclude expired/inactive docs
        result["doc_status"] = {"$eq": "active"}

        # Multi-tenant: auto-filter by department
        if user and user.department:
            result["department"] = {"$eq": user.department}

        # Permission control
        if user:
            result["permission_scope"] = {"$in": ["public"]}
            if user.roles:
                result["permission_scope"]["$in"].append("role")
                result["allowed_roles"] = {"$in": user.roles}
            if user.department:
                result["permission_scope"]["$in"].append("department")

        # Merge user-supplied filters (AND logic)
        if user_filters:
            for key, value in user_filters.items():
                # User filters override auto filters for same key
                result[key] = value

        return result if result else None

    def _reciprocal_rank_fusion(
        self, vector_results: list[SearchResult], bm25_results: list[SearchResult], k: int = 60
    ) -> list[SearchResult]:
        scores: dict[str, float] = {}
        chunk_map: dict[str, SearchResult] = {}

        for rank, r in enumerate(vector_results):
            scores[r.chunk.id] = scores.get(r.chunk.id, 0.0) + 1.0 / (k + rank + 1)
            chunk_map[r.chunk.id] = r

        for rank, r in enumerate(bm25_results):
            scores[r.chunk.id] = scores.get(r.chunk.id, 0.0) + 1.0 / (k + rank + 1)
            if r.chunk.id not in chunk_map:
                chunk_map[r.chunk.id] = r

        sorted_ids = sorted(scores, key=scores.get, reverse=True)
        results = []
        for cid in sorted_ids:
            r = chunk_map[cid]
            r.score = scores[cid]
            results.append(r)
        return results

    def _deduplicate_overlaps(self, results: list[SearchResult]) -> list[SearchResult]:
        seen_source: dict[str, SearchResult] = {}
        for r in results:
            source_id = r.chunk.overlap_source_chunk_id or r.chunk.id
            if source_id not in seen_source or r.score > seen_source[source_id].score:
                seen_source[source_id] = r
        return sorted(seen_source.values(), key=lambda x: x.score, reverse=True)
