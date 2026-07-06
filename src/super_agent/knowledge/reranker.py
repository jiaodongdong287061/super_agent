from __future__ import annotations

from super_agent.knowledge.models import SearchResult


class BGEReranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        raise RuntimeError(
            "BGEReranker requires local FlagEmbedding — removed in favor of remote embedding. "
            "Use a remote reranker API instead."
        )

    def rerank(self, query: str, results: list[SearchResult], top_k: int = 5) -> list[SearchResult]:
        raise RuntimeError("BGEReranker is no longer available — use a remote reranker API")
