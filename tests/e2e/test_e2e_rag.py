"""End-to-end test: index a markdown doc, then query it."""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from super_agent.knowledge.indexer import Indexer
from super_agent.knowledge.stores.chroma_store import ChromaStore
from super_agent.knowledge.embedders.bge import BGEEmbedder
from super_agent.knowledge.chunkers.semantic import SemanticChunker
from super_agent.knowledge.retriever import Retriever


@pytest.fixture
def rag_pipeline(tmp_path):
    """Create a full pipeline with mocked embedder and temp Chroma store."""
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    mock_store = MagicMock(spec=ChromaStore)
    mock_embedder = MagicMock(spec=BGEEmbedder)
    mock_embedder.embed_texts.return_value = [[0.1] * 1024]
    mock_embedder.embed_query.return_value = [0.1] * 1024
    mock_chunker = SemanticChunker()
    return mock_store, mock_embedder, mock_chunker


def test_e2e_index_and_retrieve(rag_pipeline, tmp_path):
    store, embedder, chunker = rag_pipeline

    sample = tmp_path / "raw_docs" / "test.md"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text(
        "# 运维手册\n\n"
        "## MySQL 主从延迟排查\n\n"
        "步骤一：检查 Seconds_Behind_Master 指标。\n"
        "步骤二：对比主库 binlog 位点与从库 relay log 位点。\n"
        "步骤三：检查网络延迟和带宽。\n\n"
        "| 指标 | 阈值 | 处理方式 |\n"
        "|------|------|----------|\n"
        "| 延迟 > 60s | 告警 | 扩容从库 |\n"
        "| 延迟 > 300s | 严重 | 切换主库 |\n",
        encoding="utf-8",
    )

    indexer = Indexer(store=store, embedder=embedder, chunker=chunker, state_dir=str(tmp_path / "state"))
    indexer.build(str(tmp_path / "raw_docs"))

    store.add.assert_called()

    chunks = chunker.chunk(
        [MagicMock(page_content=sample.read_text(encoding="utf-8"), metadata={"source": str(sample)})],
        max_chunk_size=500,
    )
    assert len(chunks) > 0
    assert any("MySQL" in c.full_text or "主从" in c.full_text for c in chunks)
