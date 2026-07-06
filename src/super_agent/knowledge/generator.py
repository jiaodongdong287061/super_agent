from __future__ import annotations

import logging
import re

import httpx

from super_agent.config import settings
from super_agent.knowledge.models import Chunk, Citation, GeneratedAnswer

logger = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[(\d+)\]")

_DEFAULT_SYSTEM_PROMPT = (
    "You are an enterprise knowledge assistant. Answer the user's question using "
    "ONLY the provided source documents. Cite sources using their numbers in "
    "brackets like [1]. If no source contains the answer, say "
    "'No relevant information found in the knowledge base.'"
)


class AnswerGenerator:
    def __init__(self) -> None:
        cfg = settings.llm
        self.api_url = f"{cfg.oneapi_base_url.rstrip('/')}/chat/completions"
        self.api_key = cfg.oneapi_api_key
        self.model = cfg.default_model
        self.temperature = cfg.default_temperature
        self.max_tokens = cfg.max_tokens
        self.timeout = cfg.request_timeout

    def generate(
        self,
        query: str,
        chunks: list[Chunk],
        system_prompt: str | None = None,
        temperature: float | None = None,
    ) -> GeneratedAnswer:
        context = self._format_context(chunks)
        messages = [
            {"role": "system", "content": system_prompt or _DEFAULT_SYSTEM_PROMPT},
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
            resp = httpx.post(
                self.api_url,
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature if temperature is not None else self.temperature,
                    "max_tokens": self.max_tokens,
                },
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
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
