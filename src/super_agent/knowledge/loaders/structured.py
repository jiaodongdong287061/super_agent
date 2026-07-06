import json
import csv
from pathlib import Path

from langchain_core.documents import Document

from super_agent.knowledge.loaders.base import BaseLoader


class JSONLoader(BaseLoader):
    def load(self, source: str) -> list[Document]:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [
                Document(page_content=json.dumps(item, ensure_ascii=False), metadata={"source": source})
                for item in data
            ]
        return [Document(page_content=json.dumps(data, ensure_ascii=False), metadata={"source": source})]

    def supported_extensions(self) -> list[str]:
        return [".json"]


class YAMLLoader(BaseLoader):
    def load(self, source: str) -> list[Document]:
        import yaml

        data = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [
                Document(page_content=yaml.dump(item, allow_unicode=True), metadata={"source": source})
                for item in data
            ]
        return [Document(page_content=yaml.dump(data, allow_unicode=True), metadata={"source": source})]

    def supported_extensions(self) -> list[str]:
        return [".yaml", ".yml"]


class CSVLoader(BaseLoader):
    def load(self, source: str) -> list[Document]:
        docs = []
        with open(source, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                content = "\n".join(f"{k}: {v}" for k, v in row.items())
                docs.append(Document(page_content=content, metadata={"source": source}))
        return docs

    def supported_extensions(self) -> list[str]:
        return [".csv"]
