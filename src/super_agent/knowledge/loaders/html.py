from pathlib import Path

from bs4 import BeautifulSoup
from langchain_core.documents import Document

from super_agent.knowledge.loaders.base import BaseLoader


class HTMLLoader(BaseLoader):
    def load(self, source: str) -> list[Document]:
        raw = Path(source).read_text(encoding="utf-8")
        soup = BeautifulSoup(raw, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return [Document(page_content=text, metadata={"source": source})]

    def supported_extensions(self) -> list[str]:
        return [".html", ".htm"]
