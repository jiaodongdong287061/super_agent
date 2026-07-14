"""Unit tests for modules missing test coverage.

Covers:
  - QueryProcessor (rewrite / expansion / intent)
  - AnswerGenerator (generation / citation parsing / fallback)
  - AuditLogger
  - FanOutRetriever
  - LLMAssistedChunker
  - PDF scanned-page (OCR) handling
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from super_agent.knowledge.models import Chunk, ProcessedQuery


# ============================================================================
# QueryProcessor
# ============================================================================

class TestQueryProcessor:
    """QueryProcessor: rewrite / expansion / intent_classification."""

    @patch("super_agent.knowledge.query_processor.settings")
    @patch("super_agent.knowledge.query_processor.LLMClient.chat")
    def test_rewrite_success(self, mock_chat, mock_settings):
        mock_settings.llm.oneapi_base_url = "http://localhost:3000/v1"
        mock_settings.llm.oneapi_api_key = "test-key"
        mock_settings.llm.default_model = "gpt-4o"
        mock_settings.llm.request_timeout = 60
        mock_settings.llm.default_temperature = 0.7
        mock_settings.llm.max_tokens = 4096
        mock_settings.rag.enable_query_rewrite = True
        mock_settings.rag.enable_query_expansion = False
        mock_settings.rag.enable_intent_classification = False

        mock_chat.return_value = {
            "choices": [{"message": {"content": "MySQL主从延迟排查步骤"}}]
        }
        from super_agent.knowledge.query_processor import QueryProcessor

        qp = QueryProcessor()
        result = qp.process("主从延迟怎么排查")
        assert result.rewritten == "MySQL主从延迟排查步骤"
        assert result.original == "主从延迟怎么排查"

    @patch("super_agent.knowledge.query_processor.settings")
    def test_rewrite_disabled(self, mock_settings):
        mock_settings.rag.enable_query_rewrite = False
        mock_settings.rag.enable_query_expansion = False
        mock_settings.rag.enable_intent_classification = False

        from super_agent.knowledge.query_processor import QueryProcessor

        qp = QueryProcessor()
        result = qp.process("test query")
        assert result.rewritten == "test query"
        assert result.expansions == []

    @patch("super_agent.knowledge.query_processor.settings")
    @patch("super_agent.knowledge.query_processor.LLMClient.chat")
    def test_expansion_success(self, mock_chat, mock_settings):
        mock_settings.llm.oneapi_base_url = "http://localhost:3000/v1"
        mock_settings.llm.oneapi_api_key = "test-key"
        mock_settings.llm.default_model = "gpt-4o"
        mock_settings.llm.request_timeout = 60
        mock_settings.llm.default_temperature = 0.7
        mock_settings.llm.max_tokens = 4096
        mock_settings.rag.enable_query_rewrite = False
        mock_settings.rag.enable_query_expansion = True
        mock_settings.rag.enable_intent_classification = False

        mock_chat.return_value = {
            "choices": [{"message": {"content":
                "1. MySQL replication lag troubleshooting\n"
                "2. How to fix MySQL slave delay\n"
                "3. MySQL主从同步延迟原因"
            }}]
        }
        from super_agent.knowledge.query_processor import QueryProcessor

        qp = QueryProcessor()
        result = qp.process("MySQL主从延迟")
        assert len(result.expansions) == 3
        assert any("troubleshooting" in e for e in result.expansions)

    @patch("super_agent.knowledge.query_processor.settings")
    @patch("super_agent.knowledge.query_processor.LLMClient.chat")
    def test_expansion_fallback_on_failure(self, mock_chat, mock_settings):
        mock_settings.llm.oneapi_base_url = "http://localhost:3000/v1"
        mock_settings.llm.oneapi_api_key = "test-key"
        mock_settings.llm.default_model = "gpt-4o"
        mock_settings.llm.request_timeout = 60
        mock_settings.llm.default_temperature = 0.7
        mock_settings.llm.max_tokens = 4096
        mock_settings.rag.enable_query_rewrite = False
        mock_settings.rag.enable_query_expansion = True
        mock_settings.rag.enable_intent_classification = False

        mock_chat.side_effect = Exception("API timeout")
        from super_agent.knowledge.query_processor import QueryProcessor

        qp = QueryProcessor()
        result = qp.process("test")
        assert result.expansions == []

    @patch("super_agent.knowledge.query_processor.settings")
    def test_intent_classification(self, mock_settings):
        mock_settings.rag.enable_query_rewrite = False
        mock_settings.rag.enable_query_expansion = False
        mock_settings.rag.enable_intent_classification = True

        from super_agent.knowledge.query_processor import QueryProcessor

        qp = QueryProcessor()
        assert qp.process("总结一下MySQL主从延迟").intent == "summarize"
        assert qp.process("如何排查主从延迟").intent == "instruction"
        assert qp.process("什么是主从延迟").intent == "qa"
        assert qp.process("随便问问").intent == "qa"

    def test_processed_query_defaults(self):
        q = ProcessedQuery(original="o", rewritten="r", expansions=[])
        assert q.language == "zh-CN"
        assert q.intent == ""


# ============================================================================
# AnswerGenerator
# ============================================================================

class TestAnswerGenerator:
    """AnswerGenerator: generation, citation parsing, fallback."""

    @patch("super_agent.knowledge.generator.settings")
    @patch("super_agent.knowledge.generator.LLMClient.chat")
    def test_generate_with_citations(self, mock_chat, mock_settings):
        mock_settings.llm.oneapi_base_url = "http://localhost:3000/v1"
        mock_settings.llm.oneapi_api_key = "test-key"
        mock_settings.llm.default_model = "gpt-4o"
        mock_settings.llm.default_temperature = 0.7
        mock_settings.llm.max_tokens = 4096
        mock_settings.llm.request_timeout = 60

        mock_chat.return_value = {
            "choices": [{"message": {"content": "根据文档[1]和[2]的说明..."}}]
        }

        from super_agent.knowledge.generator import AnswerGenerator

        chunks = [
            Chunk(id="c1", content="MySQL buffer pool", heading_chain="", full_text="...", metadata={"file_path": "doc1.md"}),
            Chunk(id="c2", content="InnoDB architecture", heading_chain="", full_text="...", metadata={"file_path": "doc2.md"}),
        ]
        gen = AnswerGenerator()
        result = gen.generate("test query", chunks)
        assert "根据文档" in result.answer_text
        assert len(result.citations) == 2

    @patch("super_agent.knowledge.generator.settings")
    @patch("super_agent.knowledge.generator.LLMClient.chat")
    def test_fallback_on_llm_failure(self, mock_chat, mock_settings):
        mock_settings.llm.oneapi_base_url = "http://localhost:3000/v1"
        mock_settings.llm.oneapi_api_key = "test-key"
        mock_settings.llm.default_model = "gpt-4o"
        mock_settings.llm.default_temperature = 0.7
        mock_settings.llm.max_tokens = 4096
        mock_settings.llm.request_timeout = 60

        mock_chat.side_effect = Exception("LLM unavailable")

        from super_agent.knowledge.generator import AnswerGenerator

        chunks = [
            Chunk(id="c1", content="test content", heading_chain="", full_text="test content", metadata={"file_path": "doc.md"}),
        ]
        gen = AnswerGenerator()
        result = gen.generate("test", chunks)
        assert "Based on 1 retrieved chunks" in result.answer_text
        assert result.citations == []

    @patch("super_agent.knowledge.generator.settings")
    @patch("super_agent.knowledge.generator.LLMClient.chat")
    def test_custom_system_prompt(self, mock_chat, mock_settings):
        mock_settings.llm.oneapi_base_url = "http://localhost:3000/v1"
        mock_settings.llm.oneapi_api_key = "test-key"
        mock_settings.llm.default_model = "gpt-4o"
        mock_settings.llm.default_temperature = 0.7
        mock_settings.llm.max_tokens = 4096
        mock_settings.llm.request_timeout = 60

        mock_chat.return_value = {
            "choices": [{"message": {"content": "custom answer"}}]
        }

        from super_agent.knowledge.generator import AnswerGenerator

        gen = AnswerGenerator()
        result = gen.generate("q", [], system_prompt="You are a MySQL expert")
        assert result.answer_text == "custom answer"

    @patch("super_agent.knowledge.generator.settings")
    @patch("super_agent.knowledge.generator.LLMClient.chat")
    def test_parse_citations_skips_invalid_indices(self, mock_chat, mock_settings):
        mock_settings.llm.oneapi_base_url = "http://localhost:3000/v1"
        mock_settings.llm.oneapi_api_key = "test-key"
        mock_settings.llm.default_model = "gpt-4o"
        mock_settings.llm.default_temperature = 0.7
        mock_settings.llm.max_tokens = 4096
        mock_settings.llm.request_timeout = 60

        mock_chat.return_value = {
            "choices": [{"message": {"content": "See [0] and [99] and [1]"}}]
        }

        from super_agent.knowledge.generator import AnswerGenerator

        chunks = [Chunk(id="c1", content="x", heading_chain="", full_text="x", metadata={"file_path": "d.md"})]
        gen = AnswerGenerator()
        result = gen.generate("q", chunks)
        # Only [1] is valid, [0] and [99] should be skipped
        assert len(result.citations) == 1


# ============================================================================
# AuditLogger
# ============================================================================

class TestAuditLogger:
    """AuditLogger: async audit logging with fire-and-forget pattern."""

    @patch("super_agent.knowledge.audit.settings")
    def test_audit_disabled_skips_write(self, mock_settings):
        mock_settings.rag.enable_audit = False

        from super_agent.knowledge.audit import AuditLogger

        audit = AuditLogger()
        result = audit.log_query(
            user_id="u1", query="test", num_chunks=2,
            chunk_ids=["c1", "c2"], answer="ans", num_citations=1,
            latency_ms=100,
        )
        # Should return None (fire-and-forget task not created when disabled)
        import asyncio
        result = asyncio.run(result)
        assert result is None

    @patch("super_agent.knowledge.audit.settings")
    @patch("super_agent.knowledge.audit.create_async_engine")
    def test_audit_write_failure_non_blocking(self, mock_engine, mock_settings):
        mock_settings.rag.enable_audit = True
        mock_settings.mysql.dsn = "mysql+asyncmy://user:pass@localhost/db"
        mock_settings.mysql.echo_sql = False

        mock_session = AsyncMock()
        mock_session.commit.side_effect = Exception("DB connection lost")
        mock_session.__aenter__.return_value = mock_session

        mock_engine.return_value.begin.return_value.__aenter__.return_value = mock_session

        from super_agent.knowledge.audit import AuditLogger

        audit = AuditLogger()
        # Should not raise despite DB failure
        import asyncio
        result = asyncio.run(
            audit.log_query("u1", "test", 1, ["c1"], "ans", 0, 100)
        )
        assert result is None


# ============================================================================
# FanOutRetriever
# ============================================================================

class TestFanOutRetriever:
    """FanOutRetriever: cross-tenant parallel retrieval + RRF merge."""

    def test_empty_stores_returns_empty(self):
        from super_agent.knowledge.fanout_retriever import FanOutRetriever

        embedder = MagicMock()
        retriever = FanOutRetriever(stores=[], embedder=embedder)
        results = retriever.retrieve("query", top_k=5)
        assert results == []

    def test_parallel_retrieval_and_rrf_merge(self):
        from super_agent.knowledge.fanout_retriever import FanOutRetriever
        from super_agent.knowledge.models import SearchResult

        embedder = MagicMock()
        embedder.embed_query.return_value = [0.1] * 1024

        store_a = MagicMock()
        store_a.search.return_value = [
            SearchResult(chunk=Chunk(id="a1", content="x", heading_chain="", full_text="x", metadata={}), score=0.9),
        ]
        store_b = MagicMock()
        store_b.search.return_value = [
            SearchResult(chunk=Chunk(id="b1", content="y", heading_chain="", full_text="y", metadata={}), score=0.8),
        ]

        retriever = FanOutRetriever(stores=[store_a, store_b], embedder=embedder)
        results = retriever.retrieve("query", top_k=5)
        assert len(results) == 2
        assert {r.id for r in results} == {"a1", "b1"}

    def test_dedup_removes_overlap_chunks(self):
        from super_agent.knowledge.fanout_retriever import FanOutRetriever
        from super_agent.knowledge.models import SearchResult

        embedder = MagicMock()
        embedder.embed_query.return_value = [0.1] * 1024

        store = MagicMock()
        store.search.return_value = [
            SearchResult(chunk=Chunk(id="a1", content="x", heading_chain="", full_text="x", metadata={}), score=0.9),
            SearchResult(
                chunk=Chunk(id="a1_overlap", content="x overlap", heading_chain="", full_text="x overlap",
                           metadata={}, is_overlap=True, overlap_source_chunk_id="a1"),
                score=0.85,
            ),
        ]

        retriever = FanOutRetriever(stores=[store], embedder=embedder)
        results = retriever.retrieve("query", top_k=5)
        # Overlap chunk should be deduplicated
        assert len(results) == 1
        assert results[0].id == "a1"


# ============================================================================
# LLMAssistedChunker
# ============================================================================

class TestLLMAssistedChunker:
    """LLMAssistedChunker: fallback, LLM boundary detection, recursive overflow."""

    def test_use_llm_false_falls_back_to_semantic(self):
        from super_agent.knowledge.chunkers.llm_assisted import LLMAssistedChunker
        from langchain_core.documents import Document

        chunker = LLMAssistedChunker(use_llm=False)
        docs = [Document(page_content="# Test\nHello world", metadata={"source": "test.md"})]
        chunks = chunker.chunk(docs)
        assert len(chunks) >= 1
        assert chunks[0].heading_chain == "Test"

    def test_llm_failure_falls_back_gracefully(self):
        from super_agent.knowledge.chunkers.llm_assisted import LLMAssistedChunker
        from langchain_core.documents import Document

        chunker = LLMAssistedChunker(use_llm=True)
        # Set invalid API URL to force failure
        chunker.api_url = "http://invalid:9999/v1"
        chunker.api_key = "test"
        chunker.model = "test"
        chunker.timeout = 1

        docs = [Document(page_content="# Test\n" + "内容 " * 1000, metadata={"source": "test.md"})]
        chunks = chunker.chunk(docs, max_chunk_size=200)
        # Should still produce chunks via fallback
        assert len(chunks) >= 1

    def test_recursive_fallback_on_overflow(self):
        """Segments exceeding max_chunk_size after LLM split should be recursively split."""
        from super_agent.knowledge.chunkers.llm_assisted import LLMAssistedChunker
        from langchain_core.documents import Document

        chunker = LLMAssistedChunker(use_llm=True)
        chunker.api_url = "http://invalid:9999/v1"
        chunker.api_key = "test"
        chunker.model = "test"
        chunker.timeout = 1

        # Long text with proper sentence boundaries so split_sentences can split it
        long_text = "MySQL主从延迟是常见问题。需要检查网络延迟。还有binlog同步状态。"
        long_text = long_text * 500
        docs = [Document(page_content="# Title\n" + long_text, metadata={"source": "test.md"})]
        chunks = chunker.chunk(docs, max_chunk_size=100)
        assert len(chunks) >= 1
        # Each chunk should respect max_chunk_size
        from super_agent.knowledge.chunkers.semantic import estimate_tokens
        for c in chunks:
            tok_count = estimate_tokens(c.full_text)
            assert tok_count <= 100 + 50, f"Chunk has {tok_count} tokens, exceeds limit"

    @patch("super_agent.knowledge.chunkers.llm_assisted.settings")
    @patch("super_agent.knowledge.chunkers.llm_assisted.LLMClient.chat")
    def test_llm_suggested_split_points(self, mock_chat, mock_settings):
        from super_agent.knowledge.chunkers.llm_assisted import LLMAssistedChunker
        from langchain_core.documents import Document

        mock_settings.llm.oneapi_base_url = "http://localhost:3000/v1"
        mock_settings.llm.oneapi_api_key = "test-key"
        mock_settings.llm.default_model = "gpt-4o"
        mock_settings.llm.request_timeout = 60
        mock_settings.llm.default_temperature = 0.7
        mock_settings.llm.max_tokens = 4096

        mock_chat.return_value = {
            "choices": [{"message": {"content": "3\n6\n9"}}]
        }

        chunker = LLMAssistedChunker(use_llm=True)
        sentences = ["a.", "b.", "c.", "d.", "e.", "f.", "g.", "h.", "i.", "j."]
        points = chunker._suggest_split_points(sentences)
        assert points == [3, 6, 9]


# ============================================================================
# PDF OCR / Scanned Page Handling
# ============================================================================

class TestPDFLoaderScannedPage:
    """PDFLoader: scanned page detection and OCR fallback."""

    @patch("super_agent.knowledge.loaders.pdf.settings")
    def test_is_scanned_page_detects_text_poor_page(self, mock_settings):
        mock_settings.ocr.enabled = True
        mock_settings.ocr.text_threshold = 0.1

        from super_agent.knowledge.loaders.pdf import PDFLoader

        loader = PDFLoader()
        # Page with very little text
        mock_page = MagicMock()
        mock_page.get_text.return_value = "  "
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        assert loader._is_scanned_page("  ", mock_page)

    @patch("super_agent.knowledge.loaders.pdf.settings")
    def test_is_scanned_page_returns_false_for_normal_page(self, mock_settings):
        mock_settings.ocr.enabled = True
        mock_settings.ocr.text_threshold = 0.1

        from super_agent.knowledge.loaders.pdf import PDFLoader

        loader = PDFLoader()
        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        assert not loader._is_scanned_page("Normal text content with enough words to fill the page area threshold", mock_page)

    @patch("super_agent.knowledge.loaders.pdf.settings")
    def test_ocr_disabled_skips_scan_detection(self, mock_settings):
        mock_settings.ocr.enabled = False

        from super_agent.knowledge.loaders.pdf import PDFLoader

        loader = PDFLoader()
        mock_page = MagicMock()
        result = loader._is_scanned_page("", mock_page)
        assert not result

    @patch("super_agent.knowledge.loaders.pdf._get_ocr_engine")
    def test_ocr_page_returns_empty_when_no_engine(self, mock_get_ocr):
        mock_get_ocr.return_value = None

        from super_agent.knowledge.loaders.pdf import PDFLoader

        loader = PDFLoader()
        result = loader._ocr_page(MagicMock())
        assert result == ""

    @patch("super_agent.knowledge.loaders.pdf._check_paddleocr")
    def test_check_paddleocr_not_available(self, mock_check):
        mock_check.return_value = False

        from super_agent.knowledge.loaders.pdf import _check_paddleocr

        # Reset cached value
        from super_agent.knowledge.loaders.pdf import _PADDLEOCR_AVAILABLE
        import super_agent.knowledge.loaders.pdf as pdf_module
        pdf_module._PADDLEOCR_AVAILABLE = None

        mock_check.return_value = False
        # Reload _check_paddleocr state
        result = _check_paddleocr()
        assert not result
