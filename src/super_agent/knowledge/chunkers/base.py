from abc import ABC, abstractmethod

from langchain_core.documents import Document

from super_agent.knowledge.models import Chunk


class BaseChunker(ABC):
    @abstractmethod
    def chunk(
        self,
        documents: list[Document],
        max_chunk_size: int = 500,
        overlap_ratio: float | None = None,
    ) -> list[Chunk]: ...

    def resolve_overlap_ratio(self, chunk_type: str, user_ratio: float | None) -> float:
        if user_ratio is not None:
            return max(0.05, min(0.30, user_ratio))
        defaults = {"text": 0.15, "table": 0.0, "code": 0.0, "list": 0.20}
        return defaults.get(chunk_type, 0.15)
