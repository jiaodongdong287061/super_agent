"""远程 Reranker API 客户端，替代不可用的本地 BGEReranker。

使用方式：
    1. 部署 services/flagembedding/ 下的推理服务
    2. 配置 SA_RERANK_API_URL 指向该服务
    3. 构造 Retriever 时传入 RemoteReranker 实例

示例：
    reranker = RemoteReranker(api_url="http://flagembedding:8001/v1/rerank")
    retriever = Retriever(store=store, embedder=embedder, reranker=reranker, ...)
"""

from __future__ import annotations

import logging

import httpx

from super_agent.config import settings
from super_agent.knowledge.models import SearchResult

logger = logging.getLogger(__name__)


class RemoteReranker:
    """通过远程 API 调用 BGE Reranker 模型进行精排。"""

    def __init__(
        self,
        api_url: str = "",
        api_key: str = "",
        top_n: int = 0,  # 0 = 返回全部候选
    ):
        self.api_url = api_url or settings.rerank.api_url
        self.api_key = api_key or settings.rerank.api_key
        self.top_n = top_n

    def rerank(
        self,
        query: str,
        results: list[SearchResult],
        top_k: int = 5,
    ) -> list[SearchResult]:
        """对检索结果进行重排序，返回精排后的 top_k 条。"""
        if not results:
            return []

        documents = [r.chunk.full_text or r.chunk.content for r in results]
        top_n = self.top_n if self.top_n > 0 else top_k

        try:
            resp = httpx.post(
                self.api_url,
                json={
                    "query": query,
                    "documents": documents,
                    "top_n": top_n,
                },
                headers=self._auth_headers(),
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

        except Exception as e:
            logger.warning("Reranker API call failed, falling back to original order: %s", e)
            return results[:top_k]

        # 将 reranker 的分数映射回原始 SearchResult
        reranked = []
        for item in data.get("results", []):
            idx = item["index"]
            if idx < len(results):
                results[idx].score = item["score"]
                reranked.append(results[idx])

        return reranked[:top_k]

    def _auth_headers(self) -> dict:
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}
