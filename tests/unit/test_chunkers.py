import pytest
from langchain_core.documents import Document
from super_agent.knowledge.chunkers.semantic import SemanticChunker


def _make_doc(text: str, source: str = "test.md", page: int | None = None) -> Document:
    meta = {"source": source}
    if page is not None:
        meta["page_numbers"] = [page]
    return Document(page_content=text, metadata=meta)


class _MockEmbedder:
    """Mock embedder returning topic-grouped vectors for semantic boundary tests."""
    def __init__(self, boundary_index: int = 5):
        self.boundary_index = boundary_index
        self.call_count = 0

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        results = []
        for i in range(len(texts)):
            if i < self.boundary_index:
                results.append([1.0, 0.0, 0.0])  # topic A
            else:
                results.append([0.0, 1.0, 0.0])  # topic B
        return results

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 1.0]

    @property
    def dimension(self) -> int:
        return 3


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


def test_semantic_boundary_no_embedder():
    """Without embedder, _find_semantic_boundaries returns empty."""
    chunker = SemanticChunker()
    sentences = [f"Sentence {i}" for i in range(8)]
    boundaries = chunker._find_semantic_boundaries(sentences)
    assert boundaries == []


def test_semantic_boundary_detection():
    """With embedder, detects topic shift between sentence groups."""
    sentences = [f"Sentence {i}" for i in range(8)]
    chunker = SemanticChunker(embedder=_MockEmbedder(boundary_index=4))
    boundaries = chunker._find_semantic_boundaries(sentences)
    assert 4 in boundaries, f"Expected boundary at index 4, got {boundaries}"


def test_semantic_chunker_splits_at_boundary():
    """With embedder, chunks should break at topic boundaries."""
    topic_a = "区块链是去中心化技术。共识机制确保安全。节点保存完整账本。数据不可篡改。智能合约执行条款。加密算法保护隐私。"
    topic_b = "人工智能模拟人类智能。机器学习是重要分支。深度学习使用神经网络。自然语言理解语言。计算机识别图像。"
    text = topic_a + topic_b  # 12 sentences, 6 per topic
    doc = _make_doc(text)
    mock = _MockEmbedder(boundary_index=6)
    chunker = SemanticChunker(embedder=mock)
    chunks = chunker.chunk([doc], max_chunk_size=50)

    assert len(chunks) >= 2, f"Expected >=2 chunks with semantic boundary, got {len(chunks)}"
    assert mock.call_count >= 1, "Embedder should have been called"
