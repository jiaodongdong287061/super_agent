import pytest
from super_agent.knowledge.models import Chunk
from super_agent.knowledge.bm25 import BM25Search


def _chunk(cid: str, text: str) -> Chunk:
    return Chunk(id=cid, content=text, heading_chain="", full_text=text, metadata={})


def test_bm25_index_and_search():
    bm25 = BM25Search()
    chunks = [
        _chunk("1", "MySQL主从延迟排查步骤"),
        _chunk("2", "Redis缓存击穿解决方案"),
        _chunk("3", "Nginx负载均衡配置"),
    ]
    bm25.index(chunks)
    results = bm25.search("MySQL延迟", top_k=2)
    assert len(results) <= 2
    assert results[0].chunk.id == "1"


def test_bm25_empty_search():
    bm25 = BM25Search()
    results = bm25.search("test", top_k=5)
    assert results == []
