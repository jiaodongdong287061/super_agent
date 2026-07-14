from __future__ import annotations

import logging

from langsmith import traceable

from super_agent.config import settings
from super_agent.knowledge.llm_client import LLMClient
from super_agent.knowledge.models import ProcessedQuery
from super_agent.prompts import get_prompt

logger = logging.getLogger(__name__)


class QueryProcessor:
    def __init__(self) -> None:
        self.llm = LLMClient()
        self.rag_cfg = settings.rag

    @traceable(name="query_processor.process", run_type="chain")
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

    @traceable(name="query_processor.rewrite", run_type="llm")
    def _rewrite(self, query: str) -> str:
        data = self.llm.chat(
            messages=[
                {"role": "user", "content": get_prompt("query_rewrite", query=query)}
            ],
            temperature=0.3,
            max_tokens=256,
        )
        return data["choices"][0]["message"]["content"].strip()

    @traceable(name="query_processor.expand", run_type="llm")
    def _expand(self, query: str) -> list[str]:
        data = self.llm.chat(
            messages=[
                {"role": "user", "content": get_prompt("query_expansion", query=query)}
            ],
            temperature=0.5,
            max_tokens=256,
        )
        text = data["choices"][0]["message"]["content"]
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
