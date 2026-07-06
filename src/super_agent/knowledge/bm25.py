from __future__ import annotations

import jieba
from rank_bm25 import BM25Okapi
from super_agent.knowledge.models import Chunk, SearchResult


class BM25Search:
    def __init__(self):
        self._bm25: BM25Okapi | None = None
        self._chunks: list[Chunk] = []

    def index(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks
        tokenized = [list(jieba.cut(c.full_text)) for c in chunks]
        self._bm25 = BM25Okapi(tokenized)

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        if not self._bm25 or not self._chunks:
            return []
        tokenized_query = list(jieba.cut(query))
        scores = self._bm25.get_scores(tokenized_query)
        scored = [(self._chunks[i], scores[i]) for i in range(len(self._chunks))]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [SearchResult(chunk=c, score=float(s)) for c, s in scored[:top_k] if s > 0]
