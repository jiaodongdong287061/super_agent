from abc import ABC, abstractmethod

from super_agent.knowledge.models import Chunk, SearchResult


class BaseVectorStore(ABC):
    @abstractmethod
    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None: ...

    @abstractmethod
    def search(
        self, query_embedding: list[float], top_k: int, filters: dict | None = None
    ) -> list[SearchResult]: ...

    @abstractmethod
    def delete(self, chunk_ids: list[str]) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def count(self) -> int: ...
