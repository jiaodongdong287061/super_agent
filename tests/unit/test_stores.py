import sys
import pytest
from unittest.mock import patch, MagicMock
from super_agent.knowledge.models import Chunk, SearchResult
from super_agent.knowledge.stores import get_store


def _sample_chunk(cid: str = "c1") -> Chunk:
    return Chunk(
        id=cid,
        content="test content",
        heading_chain="title",
        full_text="title\ntest content",
        metadata={"doc_source": "test", "chunk_type": "text", "topic_tags": ["mysql"]},
        page_numbers=[1],
    )


def test_get_store_chroma():
    with patch("super_agent.knowledge.stores.chroma_store.chromadb") as mock_chroma:
        mock_client = MagicMock()
        mock_chroma.PersistentClient.return_value = mock_client
        mock_client.get_or_create_collection.return_value = MagicMock()
        store = get_store("chroma")
        assert store is not None


def test_get_store_qdrant():
    mock_qdrant_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.collections = []
    mock_qdrant_client.return_value.get_collections.return_value = mock_resp
    mock_models = MagicMock()
    with patch.dict(sys.modules, {
        "qdrant_client": MagicMock(QdrantClient=mock_qdrant_client),
        "qdrant_client.models": mock_models,
    }):
        store = get_store("qdrant")
        assert store is not None


def test_get_store_invalid():
    with pytest.raises(ValueError, match="Unknown vector store"):
        get_store("unknown")


def test_chroma_clear():
    with patch("super_agent.knowledge.stores.chroma_store.chromadb") as mock_chroma:
        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_collection.name = "super_agent_docs"
        mock_chroma.PersistentClient.return_value = mock_client
        mock_client.get_or_create_collection.return_value = mock_collection
        store = get_store("chroma")

        store.clear()

        mock_client.delete_collection.assert_called_once_with(name="super_agent_docs")
        assert mock_client.get_or_create_collection.call_count >= 2
