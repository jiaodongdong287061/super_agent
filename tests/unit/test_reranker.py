import pytest
from super_agent.knowledge.reranker import BGEReranker


def test_bge_reranker_unavailable():
    with pytest.raises(RuntimeError, match="remote reranker"):
        BGEReranker()
