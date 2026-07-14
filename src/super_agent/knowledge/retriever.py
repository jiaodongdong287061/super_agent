from __future__ import annotations

from langsmith import traceable

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

        # 1. Vector search (always)
        vector_results = self.store.search(query_emb, top_k * 3, merged_filters)
        search_sets = [vector_results]

        # 2. ES BM25 search (if configured)
        if self.es_client:
            es_matches = self.es_client.search(query, top_k * 3)
            if es_matches:
                # Convert ES (chunk_id, score) → SearchResult
                vector_map = {r.chunk.id: r.chunk for r in vector_results}
                es_results = []
                for cid, score in es_matches:
                    chunk = vector_map.get(cid)
                    if chunk is None:
                        # ES-only hit: create minimal chunk (content will be missing)
                        chunk = Chunk(id=cid, content="", heading_chain="", full_text="", metadata={})
                    es_results.append(SearchResult(chunk=chunk, score=score))
                search_sets.append(es_results)

        # 3. Local BM25 (legacy, if configured)
        if self.use_hybrid and self.bm25:
            bm25_results = self.bm25.search(query, top_k * 3)
            search_sets.append(bm25_results)

        # 4. RRF fusion
        if len(search_sets) > 1:
            candidates = reciprocal_rank_fusion(*search_sets, k=60)
        else:
            candidates = vector_results

        # 5. Rerank (if configured)
        if self.reranker:
            candidates = self.reranker.rerank(query, candidates, top_k)

        # 6. Deduplicate overlaps
        candidates = deduplicate_overlaps(candidates)

        # 7. Try to hydrate ES-only chunks (those without content)
        #    by searching the vector store by their IDs
        empty_ids = [r.chunk.id for r in candidates if not r.chunk.content]
        if empty_ids:
            # Some stores support get_by_ids; fallback: re-search with larger top_k
            hydrated = self.store.search(query_emb, top_k * 5, merged_filters)
            hydrated_map = {r.chunk.id: r.chunk for r in hydrated}
            for r in candidates:
                if not r.chunk.content and r.chunk.id in hydrated_map:
                    r.chunk = hydrated_map[r.chunk.id]

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
                result[key] = value

        return result if result else None


def reciprocal_rank_fusion(
    *result_sets: list[SearchResult], k: int = 60
) -> list[SearchResult]:
    """Merge multiple ranked result lists using Reciprocal Rank Fusion."""
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
    """Remove overlap chunks, keeping the highest-scoring version of each source chunk."""
    seen_source: dict[str, SearchResult] = {}
    for r in results:
        source_id = r.chunk.overlap_source_chunk_id or r.chunk.id
        if source_id not in seen_source or r.score > seen_source[source_id].score:
            seen_source[source_id] = r
    return sorted(seen_source.values(), key=lambda x: x.score, reverse=True)
