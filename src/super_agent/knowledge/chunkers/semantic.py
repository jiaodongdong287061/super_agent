from __future__ import annotations

import re
import uuid

from langchain_core.documents import Document

from super_agent.knowledge.chunkers.base import BaseChunker
from super_agent.knowledge.models import Chunk
from super_agent.knowledge.metadata import build_metadata

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？\.\!\?])\s*")


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _estimate_tokens(text: str) -> int:
    chinese = sum(1 for c in text if "一" <= c <= "鿿")
    others = len(text) - chinese
    return chinese + others // 4


class SemanticChunker(BaseChunker):
    def chunk(
        self,
        documents: list[Document],
        max_chunk_size: int = 500,
        overlap_ratio: float | None = None,
    ) -> list[Chunk]:
        all_chunks: list[Chunk] = []
        for doc in documents:
            all_chunks.extend(self._chunk_document(doc, max_chunk_size, overlap_ratio))
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
            tokens = _estimate_tokens(content)
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
        lines = text.split("\n")
        sections: list[tuple[str, str]] = []
        current_chain_parts: list[str] = []
        current_content_lines: list[str] = []

        for line in lines:
            m = _HEADING_RE.match(line)
            if m:
                if current_content_lines:
                    chain = " > ".join(current_chain_parts)
                    sections.append((chain, "\n".join(current_content_lines)))
                    current_content_lines = []
                level = len(m.group(1))
                title = m.group(2).strip()
                current_chain_parts = current_chain_parts[: level - 1] + [title]
            else:
                current_content_lines.append(line)

        if current_content_lines:
            chain = " > ".join(current_chain_parts) if current_chain_parts else ""
            sections.append((chain, "\n".join(current_content_lines)))
        return sections

    def _split_large_section(
        self,
        content: str,
        heading_chain: str,
        source: str,
        doc_meta: dict,
        max_chunk_size: int,
        overlap_ratio: float | None,
    ) -> list[Chunk]:
        sentences = _split_sentences(content)
        ratio = self.resolve_overlap_ratio("text", overlap_ratio)
        overlap_tokens = int(max_chunk_size * ratio)

        chunks: list[Chunk] = []
        current_sentences: list[str] = []
        current_tokens = 0
        overlap_sentences: list[str] = []

        for sent in sentences:
            sent_tokens = _estimate_tokens(sent)
            if current_tokens + sent_tokens > max_chunk_size and current_sentences:
                chunk_text = " ".join(current_sentences)
                c = self._make_chunk(chunk_text, heading_chain, source, doc_meta)
                if overlap_sentences:
                    c.is_overlap = True
                    c.overlap_source_chunk_id = chunks[-1].id if chunks else None
                    c.overlap_ratio = ratio
                chunks.append(c)
                overlap_target = overlap_tokens
                overlap_sentences = []
                overlap_count = 0
                for s in reversed(current_sentences):
                    st = _estimate_tokens(s)
                    if overlap_count + st > overlap_target:
                        break
                    overlap_sentences.insert(0, s)
                    overlap_count += st
                current_sentences = list(overlap_sentences)
                current_tokens = overlap_count
            current_sentences.append(sent)
            current_tokens += _estimate_tokens(sent)

        if current_sentences:
            chunk_text = " ".join(current_sentences)
            c = self._make_chunk(chunk_text, heading_chain, source, doc_meta)
            if overlap_sentences and chunks:
                c.is_overlap = True
                c.overlap_source_chunk_id = chunks[-1].id
                c.overlap_ratio = ratio
            chunks.append(c)
        return chunks

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
