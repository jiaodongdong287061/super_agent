"""Elasticsearch BM25 全文检索客户端（双存储架构中的 BM25 层）。"""

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
    """Elasticsearch 客户端，封装 BM25 全文检索的索引和搜索操作。"""

    def __init__(self) -> None:
        self._client: "elasticsearch.Elasticsearch | None" = None  # type: ignore[name-defined]
        self._index_ready = False

    # ── 生命周期 ──────────────────────────────────────────

    def ensure_index(self) -> None:
        """懒初始化 ES 连接并确保索引存在。"""
        if self._index_ready:
            return
        client = self._get_client()
        index = settings.es.index_name
        if not client.indices.exists(index=index):
            client.indices.create(index=index, body=_BM25_INDEX_MAPPING)
            logger.info("ES index '%s' created with ik_smart analyzer", index)
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
        index = settings.es.index_name

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
            actions.append({"index": {"_index": index, "_id": doc["chunk_id"]}})
            actions.append(doc)
            if len(actions) >= settings.es.chunk_batch_size * 2:
                client.bulk(operations=actions, refresh=False)
                actions.clear()
        if actions:
            client.bulk(operations=actions, refresh=False)

    # ── 检索 ──────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        """BM25 全文检索，返回 (chunk_id, bm25_score) 列表。"""
        if not query.strip():
            return []
        self.ensure_index()
        client = self._get_client()

        resp = client.search(
            index=settings.es.index_name,
            body={
                "query": {
                    "multi_match": {
                        "query": query,
                        "fields": ["content^2", "heading_chain"],
                        "type": "best_fields",
                    }
                },
                "size": top_k,
            },
        )

        results: list[tuple[str, float]] = []
        for hit in resp["hits"]["hits"]:
            chunk_id = hit["_id"]
            score = float(hit["_score"])
            results.append((chunk_id, score))
        return results

    # ── 删除 ──────────────────────────────────────────────

    def delete(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        self.ensure_index()
        client = self._get_client()
        index = settings.es.index_name

        actions = [{"delete": {"_index": index, "_id": cid}} for cid in chunk_ids]
        client.bulk(operations=actions, refresh=False)

    def delete_by_file_path(self, file_path: str) -> None:
        """按文件路径删除所有关联的 chunks（用于增量索引清理）。"""
        self.ensure_index()
        client = self._get_client()
        client.delete_by_query(
            index=settings.es.index_name,
            body={"query": {"term": {"file_path": file_path}}},
            refresh=False,
        )

    def clear(self) -> None:
        self.ensure_index()
        client = self._get_client()
        client.delete_by_query(
            index=settings.es.index_name,
            body={"query": {"match_all": {}}},
            refresh=False,
        )

    def count(self) -> int:
        self.ensure_index()
        client = self._get_client()
        resp = client.count(index=settings.es.index_name)
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
