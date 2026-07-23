from __future__ import annotations

import re
import uuid

from langchain_core.documents import Document

from super_agent.knowledge.chunkers.base import BaseChunker
from super_agent.knowledge.embedders.base import BaseEmbedder
from super_agent.knowledge.models import Chunk
from super_agent.knowledge.metadata import build_metadata

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？\.\!\?])\s*")


def split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def estimate_tokens(text: str) -> int:
    chinese = sum(1 for c in text if "一" <= c <= "鿿")
    others = len(text) - chinese
    return chinese + others // 4


class SemanticChunker(BaseChunker):
    def __init__(self, embedder: BaseEmbedder | None = None):
        self.embedder = embedder

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

    def _split_oversized_sentence(self, sent: str, max_chunk_size: int) -> list[str]:
        """兜底切分：处理单句超过最大 chunk size 的情况。

        优先按换行切分（适用于目录/列表），
        最后按字符数强制切分。
        """
        # 优先尝试按换行切分
        lines = [l.strip() for l in sent.split("\n") if l.strip()]
        if len(lines) > 1:
            result: list[str] = []
            for line in lines:
                if estimate_tokens(line) <= max_chunk_size:
                    result.append(line)
                else:
                    result.extend(self._split_oversized_sentence(line, max_chunk_size))
            return result
        # 兜底：按 token 数估算边界（中文 ≈ 1 字/token）
        chunk_len = max_chunk_size
        return [sent[i:i + chunk_len] for i in range(0, len(sent), chunk_len) if sent[i:i + chunk_len].strip()]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    def _find_semantic_boundaries(self, sentences: list[str]) -> list[int]:
        """使用 Embedding 余弦相似度检测句子间的语义边界。

        返回应切分位置的句子索引列表（已排序）。
        当 embedder 不可用或句子太少时返回空列表。
        """
        _MIN_SENTENCES = 5       # 句子太少时边界检测不靠谱
        _MIN_GAP = 3             # 间隔小于此值的边界合并
        _MAX_FRACTION = 0.4      # 边界数量不超过总句数的此比例

        if not self.embedder or len(sentences) < _MIN_SENTENCES:
            return []

        embeddings = self.embedder.embed_texts(sentences)

        similarities = [
            self._cosine_similarity(embeddings[i], embeddings[i + 1])
            for i in range(len(embeddings) - 1)
        ]

        if not similarities:
            return []

        mean = sum(similarities) / len(similarities)
        variance = sum((s - mean) ** 2 for s in similarities) / len(similarities)
        std = variance ** 0.5
        threshold = mean - std

        # 收集候选边界
        candidates = [i + 1 for i, sim in enumerate(similarities) if sim < threshold]

        # 合并间隔过近的边界
        merged: list[int] = []
        for c in candidates:
            if not merged or c - merged[-1] >= _MIN_GAP:
                merged.append(c)

        # 限制边界数量上限
        max_boundaries = max(1, int(len(sentences) * _MAX_FRACTION))
        if len(merged) > max_boundaries:
            # 保留最强的边界（相似度最低的）
            scored = [(similarities[c - 1], c) for c in merged]
            scored.sort(key=lambda x: x[0])
            merged = sorted(c for _, c in scored[:max_boundaries])

        return merged

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
        # 确保单句不超过 max_chunk_size
        sentences = [
            piece
            for sent in sentences
            for piece in (self._split_oversized_sentence(sent, max_chunk_size)
                          if estimate_tokens(sent) > max_chunk_size else [sent])
        ]

        # Embedder 可用时计算语义边界
        boundaries = set(self._find_semantic_boundaries(sentences)) if self.embedder else set()

        ratio = self.resolve_overlap_ratio("text", overlap_ratio)
        overlap_tokens = int(max_chunk_size * ratio)
        min_flush_tokens = int(max_chunk_size * 0.5)

        chunks: list[Chunk] = []
        current_sentences: list[str] = []
        current_tokens = 0
        overlap_sentences: list[str] = []

        for sent_idx, sent in enumerate(sentences):
            sent_tokens = estimate_tokens(sent)
            force_flush = (
                sent_idx in boundaries
                and current_tokens >= min_flush_tokens
                and current_sentences
            )
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
                    st = estimate_tokens(s)
                    if overlap_count + st > overlap_target:
                        break
                    overlap_sentences.insert(0, s)
                    overlap_count += st
                current_sentences = list(overlap_sentences)
                current_tokens = overlap_count
            elif force_flush:
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
                    st = estimate_tokens(s)
                    if overlap_count + st > overlap_target:
                        break
                    overlap_sentences.insert(0, s)
                    overlap_count += st
                current_sentences = list(overlap_sentences)
                current_tokens = overlap_count
            current_sentences.append(sent)
            current_tokens += estimate_tokens(sent)

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
        doc_level = doc_meta.get("doc_level", "L1")
        meta = build_metadata(file_path=source, manual_tags=manual_tags, doc_level=doc_level)
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
