from __future__ import annotations

import json
import logging
import re
from collections.abc import Generator

from langsmith import traceable

from super_agent.config import settings
from super_agent.knowledge.llm_client import LLMClient
from super_agent.knowledge.models import Chunk, Citation, GeneratedAnswer
from super_agent.prompts import get_prompt

logger = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[(\d+)\]")


class AnswerGenerator:
    def __init__(self) -> None:
        self.llm = LLMClient()

    @traceable(name="answer_generator.generate", run_type="chain")
    def generate(
        self,
        query: str,
        chunks: list[Chunk],
        system_prompt: str | None = None,
        temperature: float | None = None,
    ) -> GeneratedAnswer:
        context = self._format_context(chunks)
        messages = [
            {"role": "system", "content": system_prompt or get_prompt("qa_system")},
            {
                "role": "user",
                "content": (
                    f"Question: {query}\n\n"
                    f"Source documents:\n{context}\n\n"
                    "Answer the question using ONLY the sources above. "
                    "Cite sources with [N]."
                ),
            },
        ]

        try:
            data = self.llm.chat(
                messages=messages,
                temperature=temperature,
            )
            answer_text = data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error("LLM generation failed: %s", e)
            fallback = "\n\n".join(c.full_text for c in chunks)
            answer_text = f"Based on {len(chunks)} retrieved chunks:\n{fallback[:1000]}"
            return GeneratedAnswer(answer_text=answer_text, citations=[])

        citations = self._parse_citations(answer_text, chunks)
        return GeneratedAnswer(answer_text=answer_text, citations=citations)

    def _format_context(self, chunks: list[Chunk]) -> str:
        parts: list[str] = []
        for i, chunk in enumerate(chunks, start=1):
            src = chunk.metadata.get("file_path", "unknown")
            pages = chunk.page_numbers
            page_str = f", page {pages}" if pages else ""
            snippet = chunk.full_text[:500]
            parts.append(f"[{i}] (from \"{src}\"{page_str}):\n{snippet}")
        return "\n\n".join(parts)

    def _parse_citations(self, answer_text: str, chunks: list[Chunk]) -> list[Citation]:
        seen: set[int] = set()
        citations: list[Citation] = []
        for match in _CITATION_RE.finditer(answer_text):
            idx = int(match.group(1)) - 1
            if idx in seen or idx < 0 or idx >= len(chunks):
                continue
            seen.add(idx)
            chunk = chunks[idx]
            citations.append(
                Citation(
                    chunk_id=chunk.id,
                    source_doc=chunk.metadata.get("file_path", ""),
                    page_numbers=chunk.page_numbers,
                    content_snippet=chunk.content[:200],
                )
            )
        return citations

    def generate_stream(
        self,
        query: str,
        chunks: list[Chunk],
        system_prompt: str | None = None,
        temperature: float | None = None,
    ) -> Generator[str, None, None]:
        """流式生成答案，通过 SSE 事件逐 token 产出。

        SSE 事件格式：
          data: {"type": "sources", "sources": [...]}
          data: {"type": "token", "text": "..."}
          data: {"type": "citations", "citations": [...]}
          data: {"type": "done"}
        """
        context = self._format_context(chunks)
        messages = [
            {"role": "system", "content": system_prompt or get_prompt("qa_system")},
            {
                "role": "user",
                "content": (
                    f"Question: {query}\n\n"
                    f"Source documents:\n{context}\n\n"
                    "Answer the question using ONLY the sources above. "
                    "Cite sources with [N]."
                ),
            },
        ]

        # 1. Send search results as initial event
        sources = [
            {
                "chunk_id": c.id,
                "content": c.content[:200],
                "metadata": c.metadata,
                "page_numbers": c.page_numbers,
            }
            for c in chunks
        ]
        yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"

        # 2. Stream tokens from LLM
        full_text = ""
        try:
            for token in self.llm.chat_stream(messages=messages, temperature=temperature):
                logger.debug("Stream token: len=%d repr=%r", len(token), token[:100])
                # deepseek 等部分模型在流式返回时 content 会携带累积文本而非增量
                # 检测并截取增量部分，避免前端重复拼接
                if full_text and token.startswith(full_text):
                    new_part = token[len(full_text):]
                    if not new_part:
                        continue
                    full_text = token
                    yield f"data: {json.dumps({'type': 'token', 'text': new_part})}\n\n"
                else:
                    full_text += token
                    yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
        except Exception as e:
            logger.error("Stream generation failed: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            return

        # 3. Parse citations from full answer
        citations = self._parse_citations(full_text, chunks)
        yield f"data: {json.dumps({'type': 'citations', 'citations': [{'chunk_id': c.chunk_id, 'source_doc': c.source_doc, 'page_numbers': c.page_numbers, 'content_snippet': c.content_snippet} for c in citations]})}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"
