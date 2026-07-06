from __future__ import annotations

import logging
import uuid

import httpx
from langchain_core.documents import Document

from super_agent.config import settings
from super_agent.knowledge.chunkers.base import BaseChunker
from super_agent.knowledge.chunkers.semantic import (
    SemanticChunker,
    estimate_tokens,
    split_sentences,
)
from super_agent.knowledge.metadata import build_metadata
from super_agent.knowledge.models import Chunk

logger = logging.getLogger(__name__)

_BOUNDARY_PROMPT = (
    "Split the following text into coherent segments at natural boundary points. "
    "Return only the sentence indices (0-based) where splits should occur, "
    "one per line. Each index marks the START of a new segment.\n\n"
    "Sentences:\n{sentences}"
)


class LLMAssistedChunker(BaseChunker):
    def __init__(self, use_llm: bool = True) -> None:
        self.use_llm = use_llm
        self.fallback = SemanticChunker()
        if use_llm:
            cfg = settings.llm
            self.api_url = f"{cfg.oneapi_base_url.rstrip('/')}/chat/completions"
            self.api_key = cfg.oneapi_api_key
            self.model = cfg.default_model
            self.timeout = cfg.request_timeout

    def chunk(
        self,
        documents: list[Document],
        max_chunk_size: int = 500,
        overlap_ratio: float | None = None,
    ) -> list[Chunk]:
        if not self.use_llm:
            return self.fallback.chunk(documents, max_chunk_size, overlap_ratio)

        all_chunks: list[Chunk] = []
        for doc in documents:
            all_chunks.extend(
                self._chunk_document(doc, max_chunk_size, overlap_ratio)
            )
        return all_chunks

    def _chunk_document(
        self, doc: Document, max_chunk_size: int, overlap_ratio: float | None
    ) -> list[Chunk]:
        text = doc.page_content
        source = doc.metadata.get("source", "")
        sections = self._split_by_headings(text)

        chunks: list[Chunk] = []
        for heading_chain, content in sections:
            content = content.strip()
            if not content:
                continue
            tokens = estimate_tokens(content)
            if tokens <= max_chunk_size:
                chunks.append(self._make_chunk(content, heading_chain, source, doc.metadata))
            else:
                sub_chunks = self._split_large_section(
                    content, heading_chain, source, doc.metadata, max_chunk_size, overlap_ratio
                )
                chunks.extend(sub_chunks)

        for i, c in enumerate(chunks):
            c.sibling_chunk_ids = [other.id for j, other in enumerate(chunks) if j != i]
        return chunks

    def _split_by_headings(self, text: str) -> list[tuple[str, str]]:
        return self.fallback._split_by_headings(text)

    def _split_large_section(
        self,
        content: str,
        heading_chain: str,
        source: str,
        doc_meta: dict,
        max_chunk_size: int,
        overlap_ratio: float | None,
    ) -> list[Chunk]:
        sentences = split_sentences(content)
        if len(sentences) < 2:
            return self.fallback._split_large_section(
                content, heading_chain, source, doc_meta, max_chunk_size, overlap_ratio
            )

        split_points = self._suggest_split_points(sentences)
        if not split_points:
            return self.fallback._split_large_section(
                content, heading_chain, source, doc_meta, max_chunk_size, overlap_ratio
            )

        segments = self._apply_split_points(sentences, split_points, max_chunk_size)

        chunks: list[Chunk] = []
        for seg_text in segments:
            chunks.append(self._make_chunk(seg_text, heading_chain, source, doc_meta))
        return chunks

    def _suggest_split_points(self, sentences: list[str]) -> list[int]:
        numbered = "\n".join(f"{i}: {s[:80]}" for i, s in enumerate(sentences))
        try:
            resp = httpx.post(
                self.api_url,
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": _BOUNDARY_PROMPT.format(sentences=numbered),
                        }
                    ],
                    "temperature": 0.2,
                    "max_tokens": 256,
                },
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            points: list[int] = []
            for line in text.strip().split("\n"):
                line = line.strip()
                try:
                    idx = int(line.split(":")[0].strip())
                    if 0 < idx < len(sentences):
                        points.append(idx)
                except ValueError:
                    continue
            return sorted(set(points))
        except Exception as e:
            logger.warning("LLM boundary detection failed, falling back: %s", e)
            return []

    def _apply_split_points(
        self, sentences: list[str], split_points: list[int], max_chunk_size: int
    ) -> list[str]:
        if not split_points:
            return [" ".join(sentences)]

        segments: list[str] = []
        start = 0
        for sp in sorted(split_points):
            seg = " ".join(sentences[start:sp])
            if seg.strip() and estimate_tokens(seg) <= max_chunk_size:
                segments.append(seg)
                start = sp
        remaining = " ".join(sentences[start:])
        if remaining.strip():
            segments.append(remaining)
        return segments

    def _make_chunk(
        self, content: str, heading_chain: str, source: str, doc_meta: dict
    ) -> Chunk:
        full_text = f"{heading_chain}\n{content}" if heading_chain else content
        manual_tags = doc_meta.get("manual_tags")
        meta = build_metadata(file_path=source, manual_tags=manual_tags)
        meta.update({k: v for k, v in doc_meta.items() if k not in ("source", "manual_tags")})
        meta["heading_path"] = heading_chain

        page_nums = doc_meta.get("page_numbers", [])

        return Chunk(
            id=str(uuid.uuid4()),
            content=content,
            heading_chain=heading_chain,
            full_text=full_text,
            metadata=meta,
            page_numbers=page_nums,
        )
