import pytest
from langchain_core.documents import Document
from super_agent.knowledge.chunkers.semantic import SemanticChunker


def _make_doc(text: str, source: str = "test.md", page: int | None = None) -> Document:
    meta = {"source": source}
    if page is not None:
        meta["page_numbers"] = [page]
    return Document(page_content=text, metadata=meta)


def test_heading_grouping():
    doc = _make_doc("# Title\n## 1.1 Section A\ncontent a\n## 1.2 Section B\ncontent b")
    chunker = SemanticChunker()
    chunks = chunker.chunk([doc], max_chunk_size=500)
    assert len(chunks) >= 2
    assert any("Section A" in c.heading_chain for c in chunks)
    assert any("Section B" in c.heading_chain for c in chunks)


def test_heading_inheritance():
    doc = _make_doc("# 运维手册\n## 1.1 排查步骤\n步骤一：检查指标。\n步骤二：对比位点。")
    chunker = SemanticChunker()
    chunks = chunker.chunk([doc], max_chunk_size=50)
    for c in chunks:
        if "排查步骤" in c.heading_chain:
            assert "运维手册" in c.heading_chain
            break


def test_overlap_ratio():
    long_text = "".join(f"这是第{i}个用于测试重叠切分功能的较长句子。" for i in range(40))
    doc = _make_doc(long_text)
    chunker = SemanticChunker()
    chunks = chunker.chunk([doc], max_chunk_size=200, overlap_ratio=0.20)
    assert len(chunks) > 1
    overlapping = [c for c in chunks if c.is_overlap]
    assert len(overlapping) > 0


def test_chunk_has_page_numbers():
    doc = _make_doc("# Page\ncontent", page=5)
    chunker = SemanticChunker()
    chunks = chunker.chunk([doc], max_chunk_size=500)
    assert chunks[0].page_numbers == [5]


def test_chunk_metadata_includes_topic_tags():
    doc = Document(
        page_content="# Test\ncontent",
        metadata={"source": "raw_docs/SRE/mysql/runbook.md"},
    )
    chunker = SemanticChunker()
    chunks = chunker.chunk([doc], max_chunk_size=500)
    assert "SRE" in chunks[0].metadata.get("topic_tags", [])
    assert "mysql" in chunks[0].metadata.get("topic_tags", [])


def test_chunk_carries_manual_tags():
    doc = Document(
        page_content="这是一段测试文本内容，用于验证 manual_tags 的传递。",
        metadata={"source": "raw_docs/test/doc.md", "manual_tags": ["自定义标签"]},
    )
    chunker = SemanticChunker()
    chunks = chunker.chunk([doc])
    assert len(chunks) > 0
    assert "自定义标签" in chunks[0].metadata["topic_tags"]
