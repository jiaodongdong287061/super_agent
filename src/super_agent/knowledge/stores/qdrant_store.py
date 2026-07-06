from __future__ import annotations

from super_agent.config import settings
from super_agent.knowledge.models import Chunk, SearchResult
from super_agent.knowledge.stores.base import BaseVectorStore


class QdrantStore(BaseVectorStore):
    def __init__(self):
        from qdrant_client import QdrantClient

        cfg = settings.vector_store
        client_kwargs: dict = {"url": cfg.qdrant_url, "prefer_grpc": False}
        if cfg.qdrant_api_key:
            client_kwargs["api_key"] = cfg.qdrant_api_key

        self.client = QdrantClient(**client_kwargs)
        self.collection_name = cfg.qdrant_collection
        self._ensure_collection()

    def _ensure_collection(self):
        from qdrant_client.models import Distance, VectorParams

        cfg = settings.vector_store
        collections = [c.name for c in self.client.get_collections().collections]
        if self.collection_name not in collections:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=cfg.qdrant_vector_size, distance=getattr(Distance, cfg.qdrant_distance)),
            )

    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        from qdrant_client.models import PointStruct

        if not chunks:
            return
        points = [
            PointStruct(
                id=c.id,
                vector=e,
                payload={"full_text": c.full_text, **c.metadata},
            )
            for c, e in zip(chunks, embeddings)
        ]
        self.client.upsert(collection_name=self.collection_name, points=points)

    def search(
        self, query_embedding: list[float], top_k: int = 5, filters: dict | None = None
    ) -> list[SearchResult]:
        from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny

        qdrant_filter = None
        if filters:
            conditions = []
            for key, value in filters.items():
                if key == "topic_tags" and isinstance(value, dict) and "$contains" in value:
                    conditions.append(FieldCondition(key=key, match=MatchAny(any=[value["$contains"]])))
                elif isinstance(value, dict) and "$in" in value:
                    conditions.append(FieldCondition(key=key, match=MatchAny(any=value["$in"])))
                else:
                    conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
            qdrant_filter = Filter(must=conditions)

        results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_embedding,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        ).points

        search_results = []
        for hit in results:
            payload = hit.payload or {}
            full_text = payload.pop("full_text", "")
            chunk = Chunk(
                id=str(hit.id),
                content=full_text,
                heading_chain=payload.get("heading_path", ""),
                full_text=full_text,
                metadata=payload,
            )
            search_results.append(SearchResult(chunk=chunk, score=hit.score))
        return search_results

    def delete(self, chunk_ids: list[str]) -> None:
        if chunk_ids:
            from qdrant_client.models import Filter, FieldCondition, MatchAny
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=Filter(must=[
                    FieldCondition(key="id", match=MatchAny(any=chunk_ids)),
                ]),
            )

    def clear(self) -> None:
        self.client.delete_collection(collection_name=self.collection_name)
        self._ensure_collection()

    def count(self) -> int:
        info = self.client.get_collection(self.collection_name)
        return info.points_count or 0
