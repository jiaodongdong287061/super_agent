from __future__ import annotations

import concurrent.futures

from super_agent.knowledge.models import Chunk
from super_agent.knowledge.retriever import deduplicate_overlaps, reciprocal_rank_fusion
from super_agent.knowledge.embedders.base import BaseEmbedder
from super_agent.knowledge.stores.base import BaseVectorStore


class FanOutRetriever:
    """跨租户检索器：并行查询多个租户集合，通过 RRF 合并结果。

    用于未指定 tenant_id 的场景（如管理员跨租户搜索）。
    每个租户的数据始终存储在自己的独立集合中，不混入公共集合。
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

        # 并行查询所有租户的向量库
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.stores)) as pool:
            futures = [
                pool.submit(self._search_store, store, query_emb, top_k, filters)
                for store in self.stores
            ]
            all_results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # 用 RRF 合并结果
        merged = reciprocal_rank_fusion(*all_results, k=60)

        # 去重重叠 chunk
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
