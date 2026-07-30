"""Elasticsearch BM25 全文检索客户端（双存储架构中的 BM25 层）。

每个部门/租户使用独立索引，索引命名规则：
  super_agent_docs            — 公共索引
  super_agent_docs_{tenant}   — 部门/租户索引
"""

from __future__ import annotations

import logging

from super_agent.config import settings
from super_agent.knowledge.models import Chunk

logger = logging.getLogger(__name__)

# ES BM25 搜索的索引 mapping，中文用 ik_smart 分词
_BM25_INDEX_MAPPING = {
    "settings": {
        "analysis": {
            "analyzer": {
                "ik_analyzer": {
                    "type": "custom",
                    "tokenizer": "ik_smart",
                }
            }
        },
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
    "mappings": {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "content": {"type": "text", "analyzer": "ik_analyzer"},
            "heading_chain": {"type": "text", "analyzer": "ik_analyzer"},
            "file_path": {"type": "keyword"},
            "doc_type": {"type": "keyword"},
            "department": {"type": "keyword"},
            "topic_tags": {"type": "keyword"},
            "chunk_type": {"type": "keyword"},
            "page_numbers": {"type": "integer"},
            "doc_version": {"type": "keyword"},
        }
    },
}


class ESClient:
    """Elasticsearch 客户端，封装 BM25 全文检索的索引和搜索操作。

    每个实例绑定一个索引（公共或部门级），通过 tenant_id 区分。
    """

    def __init__(self, tenant_id: str = "") -> None:
        self._client: "elasticsearch.Elasticsearch | None" = None  # type: ignore[name-defined]
        self._index_ready = False
        self.index_name = settings.es.index_name
        if tenant_id:
            self.index_name = f"{self.index_name}_{tenant_id}"

    # ── 生命周期 ──────────────────────────────────────────

    def ensure_index(self) -> None:
        """懒初始化 ES 连接并确保索引存在。"""
        if self._index_ready:
            return
        client = self._get_client()
        if not client.indices.exists(index=self.index_name):
            client.indices.create(index=self.index_name, body=_BM25_INDEX_MAPPING)
            logger.info("ES index '%s' created with ik_smart analyzer", self.index_name)
        self._index_ready = True

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._index_ready = False

    # ── 写入 ──────────────────────────────────────────────

    def add(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        self.ensure_index()
        client = self._get_client()

        batch: list[dict] = []
        for c in chunks:
            batch.append(
                {
                    "chunk_id": c.id,
                    "content": c.content,
                    "heading_chain": c.heading_chain,
                    "file_path": c.metadata.get("file_path", ""),
                    "doc_type": c.metadata.get("doc_type", ""),
                    "department": c.metadata.get("department", ""),
                    "topic_tags": c.metadata.get("topic_tags", []),
                    "chunk_type": c.metadata.get("chunk_type", "text"),
                    "page_numbers": c.metadata.get("page_numbers", []),
                    "doc_version": c.metadata.get("doc_version", ""),
                }
            )

        actions = []
        for doc in batch:
            actions.append({"index": {"_index": self.index_name, "_id": doc["chunk_id"]}})
            actions.append(doc)
            if len(actions) >= settings.es.chunk_batch_size * 2:
                client.bulk(operations=actions, refresh=False)
                actions.clear()
        if actions:
            client.bulk(operations=actions, refresh=False)

        logger.info("ES indexed %d chunks -> %s", len(batch), self.index_name)

    # ── 检索 ──────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float, dict]]:
        """BM25 全文检索，返回 (chunk_id, bm25_score, source_data) 列表。"""
        if not query.strip():
            return []
        self.ensure_index()
        client = self._get_client()

        body: dict = {
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": ["content^2", "heading_chain"],
                    "type": "best_fields",
                    "minimum_should_match": settings.es.bm25_minimum_should_match,
                }
            },
            "size": top_k,
        }
        if settings.es.bm25_score_threshold > 0:
            body["min_score"] = settings.es.bm25_score_threshold

        resp = client.search(index=self.index_name, body=body)

        results: list[tuple[str, float, dict]] = []
        for hit in resp["hits"]["hits"]:
            chunk_id = hit["_id"]
            score = float(hit["_score"])
            source = hit.get("_source", {})
            results.append((chunk_id, score, source))
        return results

    # ── 删除 ──────────────────────────────────────────────

    def delete(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        self.ensure_index()
        client = self._get_client()

        actions = [{"delete": {"_index": self.index_name, "_id": cid}} for cid in chunk_ids]
        client.bulk(operations=actions, refresh=False)

    def delete_by_file_path(self, file_path: str) -> None:
        """按文件路径删除所有关联的 chunks（用于增量索引清理）。"""
        self.ensure_index()
        client = self._get_client()
        client.delete_by_query(
            index=self.index_name,
            body={"query": {"term": {"file_path": file_path}}},
            refresh=False,
        )

    def clear(self) -> None:
        self.ensure_index()
        client = self._get_client()
        client.delete_by_query(
            index=self.index_name,
            body={"query": {"match_all": {}}},
            refresh=False,
        )

    def count(self) -> int:
        self.ensure_index()
        client = self._get_client()
        resp = client.count(index=self.index_name)
        return int(resp["count"])

    # ── 内部 ──────────────────────────────────────────────

    def _get_client(self) -> "elasticsearch.Elasticsearch":  # type: ignore[name-defined]
        if self._client is not None:
            return self._client

        from elasticsearch import Elasticsearch

        cfg = settings.es
        conn_kwargs: dict = {"hosts": cfg.hosts}
        if cfg.ca_certs:
            conn_kwargs["ca_certs"] = cfg.ca_certs
        if cfg.username and cfg.password:
            conn_kwargs["basic_auth"] = (cfg.username, cfg.password)

        self._client = Elasticsearch(**conn_kwargs)
        return self._client
