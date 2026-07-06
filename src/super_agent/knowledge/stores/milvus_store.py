from __future__ import annotations

from super_agent.config import settings
from super_agent.knowledge.models import Chunk, SearchResult
from super_agent.knowledge.stores.base import BaseVectorStore


class MilvusStore(BaseVectorStore):
    def __init__(self):
        from pymilvus import MilvusClient
        cfg = settings.vector_store
        self.client = MilvusClient(
            uri=f"http://{cfg.milvus_host}:{cfg.milvus_port}"
        )
        self.collection_name = cfg.milvus_collection
        self._ensure_collection()

    def _ensure_collection(self):
        if not self.client.has_collection(self.collection_name):
            from pymilvus import CollectionSchema, FieldSchema, DataType
            fields = [
                FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=128, is_primary=True),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1024),
                FieldSchema(name="full_text", dtype=DataType.VARCHAR, max_length=65535),
            ]
            schema = CollectionSchema(fields=fields)
            self.client.create_collection(
                collection_name=self.collection_name, schema=schema
            )

    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        if not chunks:
            return
        data = [
            {"id": c.id, "embedding": e, "full_text": c.full_text, **c.metadata}
            for c, e in zip(chunks, embeddings)
        ]
        self.client.insert(collection_name=self.collection_name, data=data)

    def search(
        self, query_embedding: list[float], top_k: int = 5, filters: dict | None = None
    ) -> list[SearchResult]:
        results = self.client.search(
            collection_name=self.collection_name,
            data=[query_embedding],
            limit=top_k,
            output_fields=["full_text"],
            filter=self._build_filter(filters) if filters else "",
        )
        search_results = []
        if results and results[0]:
            for hit in results[0]:
                meta = {k: v for k, v in hit["entity"].items() if k != "full_text"}
                chunk = Chunk(
                    id=hit["id"],
                    content=hit["entity"].get("full_text", ""),
                    heading_chain=meta.get("heading_path", ""),
                    full_text=hit["entity"].get("full_text", ""),
                    metadata=meta,
                )
                search_results.append(SearchResult(chunk=chunk, score=hit["distance"]))
        return search_results

    def delete(self, chunk_ids: list[str]) -> None:
        if chunk_ids:
            self.client.delete(
                collection_name=self.collection_name,
                filter=f'id in {chunk_ids}',
            )

    def clear(self) -> None:
        self.client.drop_collection(collection_name=self.collection_name)
        self._ensure_collection()

    def count(self) -> int:
        stats = self.client.get_collection_stats(self.collection_name)
        return int(stats.get("row_count", 0))

    def _build_filter(self, filters: dict) -> str:
        parts = []
        for key, value in filters.items():
            if key == "topic_tags" and isinstance(value, dict) and "$contains" in value:
                parts.append(f'array_contains(topic_tags, "{value["$contains"]}")')
            elif isinstance(value, dict) and "$in" in value:
                vals = ", ".join(f'"{v}"' for v in value["$in"])
                parts.append(f'{key} in [{vals}]')
            else:
                parts.append(f'{key} == "{value}"')
        return " and ".join(parts)
