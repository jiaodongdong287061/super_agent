import pytest
from unittest.mock import MagicMock, patch
from super_agent.knowledge.models import Chunk, SearchResult
from super_agent.knowledge.retriever import Retriever


def _chunk(cid: str, text: str, **kwargs) -> Chunk:
    return Chunk(id=cid, content=text, heading_chain="", full_text=text, metadata={"topic_tags": ["mysql"]}, **kwargs)


def test_retriever_dedup_overlaps():
    store = MagicMock()
    embedder = MagicMock()
    embedder.embed_query.return_value = [0.1] * 1024

    c1 = _chunk("a", "text a")
    c2 = _chunk("b", "overlap text", is_overlap=True, overlap_source_chunk_id="a")
    store.search.return_value = [
        SearchResult(chunk=c1, score=0.9),
        SearchResult(chunk=c2, score=0.85),
    ]

    retriever = Retriever(store=store, embedder=embedder)
    results = retriever._deduplicate_overlaps(store.search.return_value)
    assert len(results) == 1
    assert results[0].chunk.id == "a"


def test_retriever_rrf_fusion():
    store = MagicMock()
    embedder = MagicMock()
    embedder.embed_query.return_value = [0.1] * 1024
    store.search.return_value = [SearchResult(chunk=_chunk("a", "x"), score=0.9)]

    bm25 = MagicMock()
    bm25.search.return_value = [SearchResult(chunk=_chunk("b", "y"), score=1.5)]

    retriever = Retriever(store=store, embedder=embedder, bm25=bm25, use_hybrid=True)
    with patch.object(retriever, "_deduplicate_overlaps", side_effect=lambda x: x):
        results = retriever.retrieve("query", top_k=5)
    assert len(results) <= 5
