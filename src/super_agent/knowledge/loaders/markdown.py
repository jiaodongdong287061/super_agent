from pathlib import Path

from langchain_core.documents import Document

from super_agent.knowledge.loaders.base import BaseLoader


class MarkdownLoader(BaseLoader):
    def load(self, source: str) -> list[Document]:
        text = Path(source).read_text(encoding="utf-8")
        return [Document(page_content=text, metadata={"source": source})]

    def supported_extensions(self) -> list[str]:
        return [".md", ".markdown"]
