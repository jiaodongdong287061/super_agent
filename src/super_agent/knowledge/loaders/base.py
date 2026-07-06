from abc import ABC, abstractmethod

from langchain_core.documents import Document


class BaseLoader(ABC):
    @abstractmethod
    def load(self, source: str) -> list[Document]: ...

    @abstractmethod
    def supported_extensions(self) -> list[str]: ...
