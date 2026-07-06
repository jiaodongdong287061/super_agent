from super_agent.knowledge.models import Chunk, SearchResult, MetadataSchema


def test_chunk_creation():
    c = Chunk(
        id="test-1",
        content="hello world",
        heading_chain="1 > 1.1 intro",
        full_text="1 > 1.1 intro\nhello world",
        metadata={"doc_source": "local_file", "chunk_type": "text"},
    )
    assert c.id == "test-1"
    assert c.heading_chain == "1 > 1.1 intro"
    assert c.is_overlap is False
    assert c.overlap_source_chunk_id is None
    assert c.page_numbers == []


def test_chunk_with_overlap():
    c = Chunk(
        id="test-2",
        content="overlapping content",
        heading_chain="",
        full_text="overlapping content",
        metadata={"chunk_type": "text"},
        is_overlap=True,
        overlap_source_chunk_id="test-1",
        overlap_ratio=0.15,
    )
    assert c.is_overlap is True
    assert c.overlap_source_chunk_id == "test-1"


def test_search_result():
    c = Chunk(id="a", content="x", heading_chain="", full_text="x", metadata={})
    r = SearchResult(chunk=c, score=0.95)
    assert r.score == 0.95


def test_metadata_schema_defaults():
    m = MetadataSchema()
    assert m.doc_source == "local_file"
    assert m.topic_tags == []
    assert m.page_numbers == []
