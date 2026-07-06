from __future__ import annotations

import logging

import httpx

from super_agent.config import settings
from super_agent.knowledge.models import ProcessedQuery

logger = logging.getLogger(__name__)

_REWRITE_PROMPT = (
    "You are a query rewriting assistant. Improve the following search query "
    "for knowledge base retrieval. Correct typos, expand abbreviations, "
    "and handle mixed Chinese-English terms. Return ONLY the rewritten query, "
    "nothing else.\n\nOriginal query: {query}"
)

_EXPANSION_PROMPT = (
    "You are a query expansion assistant. Given a search query, generate "
    "2-3 alternative phrasings that express the same information need. "
    "Return each phrasing on a separate line, numbered. "
    "Keep each version concise.\n\nQuery: {query}"
)


class QueryProcessor:
    def __init__(self) -> None:
        cfg = settings.llm
        self.api_url = f"{cfg.oneapi_base_url.rstrip('/')}/chat/completions"
        self.api_key = cfg.oneapi_api_key
        self.model = cfg.default_model
        self.timeout = cfg.request_timeout
        self.rag_cfg = settings.rag

    def process(self, query: str) -> ProcessedQuery:
        rewritten = query
        expansions: list[str] = []

        if self.rag_cfg.enable_query_rewrite:
            try:
                rewritten = self._rewrite(query)
            except Exception as e:
                logger.warning("Query rewrite failed, using original: %s", e)

        if self.rag_cfg.enable_query_expansion:
            try:
                expansions = self._expand(rewritten)
            except Exception as e:
                logger.warning("Query expansion failed: %s", e)

        intent = ""
        if self.rag_cfg.enable_intent_classification:
            intent = self._classify_intent(rewritten)

        return ProcessedQuery(
            original=query,
            rewritten=rewritten,
            expansions=expansions,
            intent=intent,
        )

    def _rewrite(self, query: str) -> str:
        resp = httpx.post(
            self.api_url,
            json={
                "model": self.model,
                "messages": [
                    {"role": "user", "content": _REWRITE_PROMPT.format(query=query)}
                ],
                "temperature": 0.3,
                "max_tokens": 256,
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    def _expand(self, query: str) -> list[str]:
        resp = httpx.post(
            self.api_url,
            json={
                "model": self.model,
                "messages": [
                    {"role": "user", "content": _EXPANSION_PROMPT.format(query=query)}
                ],
                "temperature": 0.5,
                "max_tokens": 256,
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        expansions = [
            line.strip().lstrip("0123456789. ") for line in text.split("\n") if line.strip()
        ]
        return expansions[:3]

    def _classify_intent(self, query: str) -> str:
        query_lower = query.lower()
        summary_keywords = ["summary", "summarize", "总结", "概括", "摘要"]
        instruction_keywords = ["how to", "how do i", "steps", "步骤", "怎么做", "如何"]
        qa_keywords = ["what is", "what are", "why", "when", "where", "是什么", "为什么", "什么时候"]

        for kw in summary_keywords:
            if kw in query_lower:
                return "summarize"
        for kw in instruction_keywords:
            if kw in query_lower:
                return "instruction"
        for kw in qa_keywords:
            if kw in query_lower:
                return "qa"

        return "qa"
