from unittest.mock import patch

from fastapi.testclient import TestClient


def _build_client():
    from super_agent.main import app
    return TestClient(app)


class TestRagDeleteEndpoint:
    def test_delete_by_chunk_ids(self):
        client = _build_client()
        resp = client.post("/rag/delete", json={"chunk_ids": ["id1", "id2"]})
        assert resp.status_code in (200, 503)
        if resp.status_code == 200:
            data = resp.json()
            assert "status" in data
            assert "deleted_count" in data

    def test_clear_all(self):
        client = _build_client()
        resp = client.post("/rag/delete", json={})
        assert resp.status_code in (200, 503)
        if resp.status_code == 200:
            data = resp.json()
            assert data["status"] == "ok"

    def test_delete_response_structure(self):
        from super_agent.main import app

        mock_store = type("MockStore", (), {
            "count": lambda self: 5,
            "delete": lambda self, ids: None,
            "clear": lambda self: None,
        })()

        with patch("super_agent.knowledge.stores.get_store", return_value=mock_store):
            client = TestClient(app)
            resp = client.post("/rag/delete", json={"chunk_ids": ["id1"]})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["deleted_count"] == 0

    def test_clear_all_with_mock(self):
        from super_agent.main import app

        mock_store = type("MockStore", (), {
            "count": lambda self: 10,
            "delete": lambda self, ids: None,
            "clear": lambda self: None,
        })()

        with patch("super_agent.knowledge.stores.get_store", return_value=mock_store):
            client = TestClient(app)
            resp = client.post("/rag/delete", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
