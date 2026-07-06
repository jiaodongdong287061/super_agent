import pytest
from fastapi.testclient import TestClient


def test_health_endpoint():
    from super_agent.main import app
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_rag_query_endpoint_structure():
    from super_agent.main import app
    client = TestClient(app)
    resp = client.post("/rag/query", json={"query": "test", "top_k": 3})
    assert resp.status_code in (200, 503)
