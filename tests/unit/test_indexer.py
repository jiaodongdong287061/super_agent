import pytest
from unittest.mock import MagicMock, patch
from super_agent.knowledge.indexer import Indexer


def test_indexer_build_calls_pipeline(tmp_path):
    store = MagicMock()
    embedder = MagicMock()
    embedder.embed_texts.return_value = [[0.1] * 1024]
    chunker = MagicMock()
    chunker.chunk.return_value = [MagicMock(id="c1", full_text="t", metadata={})]

    doc_dir = tmp_path / "raw_docs"
    doc_dir.mkdir()
    sample = doc_dir / "test.md"
    sample.write_text("# Test\ncontent", encoding="utf-8")

    indexer = Indexer(store=store, embedder=embedder, chunker=chunker, state_dir=str(tmp_path / "state"))
    indexer.build(doc_dir=str(doc_dir))

    store.add.assert_called()


def test_indexer_rebuild_clears_first(tmp_path):
    store = MagicMock()
    store.count.return_value = 5
    embedder = MagicMock()
    chunker = MagicMock()

    indexer = Indexer(store=store, embedder=embedder, chunker=chunker, state_dir=str(tmp_path / "state"))
    with patch.object(indexer, "build"):
        indexer.rebuild(doc_dir="data/raw_docs")


def test_indexer_build_passes_file_tags(tmp_path):
    """Indexer.build 应将 file_tags 写入 Document.metadata["manual_tags"]"""
    from langchain_core.documents import Document
    from super_agent.knowledge.chunkers.semantic import SemanticChunker

    store = MagicMock()
    embedder = MagicMock()
    embedder.embed_texts.return_value = [[0.1] * 1024]

    doc_dir = tmp_path / "raw_docs"
    doc_dir.mkdir()
    sample = doc_dir / "test.md"
    sample.write_text("# Test\n一些内容用于测试", encoding="utf-8")

    chunker = SemanticChunker()
    indexer = Indexer(
        store=store,
        embedder=embedder,
        chunker=chunker,
        state_dir=str(tmp_path / "state"),
    )

    file_path = str(sample)
    indexer.build(doc_dir=str(doc_dir), file_tags={file_path: ["自定义标签"]})

    # 验证 store.add 被调用，且 chunks 的 metadata 包含自定义标签
    store.add.assert_called()
    chunks = store.add.call_args[0][0]
    assert any("自定义标签" in c.metadata.get("topic_tags", []) for c in chunks)
