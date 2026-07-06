import pytest
from unittest.mock import MagicMock, patch
from super_agent.knowledge.embedders import get_embedder


def test_get_embedder_api():
    with patch("super_agent.knowledge.embedders.api.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"embedding": [0.1] * 1024, "index": 0}]}
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp
        embedder = get_embedder("api")
        result = embedder.embed_query("test")
        assert len(result) == 1024


def test_get_embedder_invalid():
    with pytest.raises(ValueError, match="Unknown embedder"):
        get_embedder("unknown")


def test_get_embedder_bge_unavailable():
    with pytest.raises(ValueError, match="Unknown embedder"):
        get_embedder("bge")
