from __future__ import annotations

import concurrent.futures

from super_agent.knowledge.models import Chunk
from super_agent.knowledge.retriever import deduplicate_overlaps, reciprocal_rank_fusion
from super_agent.knowledge.embedders.base import BaseEmbedder
from super_agent.knowledge.stores.base import BaseVectorStore


class FanOutRetriever:
    """Cross-tenant retriever that queries multiple tenant collections in parallel
    and merges results using Reciprocal Rank Fusion (RRF).

    Used when no tenant_id is specified (e.g., admin cross-tenant search).
    Data never exists in a shared collection — each tenant's data stays
    in its own isolated collection.
    """

    def __init__(self, stores: list[BaseVectorStore], embedder: BaseEmbedder):
        self.stores = stores
        self.embedder = embedder

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
        **kwargs,
    ) -> list[Chunk]:
        if not self.stores:
            return []

        query_emb = self.embedder.embed_query(query)

        # Parallel query across all tenant stores
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.stores)) as pool:
            futures = [
                pool.submit(self._search_store, store, query_emb, top_k, filters)
                for store in self.stores
            ]
            all_results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # Merge with RRF
        merged = reciprocal_rank_fusion(*all_results, k=60)

        # Deduplicate overlap chunks
        merged = deduplicate_overlaps(merged)

        return [r.chunk for r in merged[:top_k]]

    @staticmethod
    def _search_store(
        store: BaseVectorStore,
        query_emb: list[float],
        top_k: int,
        filters: dict | None,
    ) -> list:
        from super_agent.knowledge.models import SearchResult

        return store.search(query_emb, top_k * 3, filters)
