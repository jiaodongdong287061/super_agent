from __future__ import annotations

import chromadb
from super_agent.config import settings
from super_agent.knowledge.models import Chunk, SearchResult
from super_agent.knowledge.stores.base import BaseVectorStore


class ChromaStore(BaseVectorStore):
    def __init__(self, persist_dir: str | None = None):
        cfg = settings.vector_store
        self.client = chromadb.PersistentClient(path=persist_dir or cfg.chroma_persist_dir)
        self.collection = self.client.get_or_create_collection(
            name="super_agent_docs",
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        if not chunks:
            return
        ids = [c.id for c in chunks]
        docs = [c.full_text for c in chunks]
        metas = [c.metadata for c in chunks]
        self.collection.add(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)

    def search(
        self, query_embedding: list[float], top_k: int = 5, filters: dict | None = None
    ) -> list[SearchResult]:
        where = self._build_where(filters) if filters else None
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        search_results = []
        if results["ids"] and results["ids"][0]:
            for i, cid in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                chunk = Chunk(
                    id=cid,
                    content=results["documents"][0][i] if results["documents"] else "",
                    heading_chain=meta.get("heading_path", ""),
                    full_text=results["documents"][0][i] if results["documents"] else "",
                    metadata=meta,
                )
                score = 1.0 - results["distances"][0][i] if results["distances"] else 0.0
                search_results.append(SearchResult(chunk=chunk, score=score))
        return search_results

    def delete(self, chunk_ids: list[str]) -> None:
        if chunk_ids:
            self.collection.delete(ids=chunk_ids)

    def clear(self) -> None:
        self.client.delete_collection(name=self.collection.name)
        self.collection = self.client.get_or_create_collection(
            name="super_agent_docs",
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        return self.collection.count()

    def _build_where(self, filters: dict) -> dict:
        where = {}
        for key, value in filters.items():
            if key == "topic_tags" and isinstance(value, dict) and "$contains" in value:
                where[key] = value
            elif isinstance(value, dict):
                where[key] = value
            else:
                where[key] = {"$eq": value}
        return where
