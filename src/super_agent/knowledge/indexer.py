from __future__ import annotations

import hashlib
import json
from pathlib import Path

from super_agent.knowledge.stores.base import BaseVectorStore
from super_agent.knowledge.embedders.base import BaseEmbedder
from super_agent.knowledge.chunkers.base import BaseChunker
from super_agent.knowledge.loaders import get_loader, supported_extensions
from super_agent.knowledge.tags import parse_tags_yaml, match_file_tags


class Indexer:
    def __init__(
        self,
        store: BaseVectorStore,
        embedder: BaseEmbedder,
        chunker: BaseChunker,
        state_dir: str = "./data/index_state",
    ):
        self.store = store
        self.embedder = embedder
        self.chunker = chunker
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "index_state.json"

    def build(self, doc_dir: str, file_tags: dict[str, list[str]] | None = None, **kwargs) -> None:
        doc_path = Path(doc_dir)
        state = self._load_state()

        # 自动检测 tags.yaml
        tags_yaml_path = doc_path / "tags.yaml"
        yaml_tags = parse_tags_yaml(tags_yaml_path)

        for fp in doc_path.rglob("*"):
            if not fp.is_file() or fp.suffix.lower() not in supported_extensions():
                continue
            if fp.name == "tags.yaml":
                continue

            file_hash = self._file_hash(fp)
            rel_path = str(fp)

            if state.get(rel_path) == file_hash:
                continue

            loader = get_loader(fp.suffix.lower())
            documents = loader.load(str(fp))

            # 合并: 调用方 file_tags + tags.yaml 匹配
            manual_tags = file_tags.get(str(fp), []) if file_tags else []
            # 规范化路径为正斜杠以匹配 tags.yaml 中的键
            norm_path = str(fp).replace("\\", "/")
            yaml_matched = match_file_tags(norm_path, yaml_tags)
            merged = manual_tags + [t for t in yaml_matched if t not in manual_tags]

            for doc in documents:
                doc.metadata["manual_tags"] = merged

            chunks = self.chunker.chunk(documents, **kwargs)

            if chunks:
                texts = [c.full_text for c in chunks]
                embeddings = self.embedder.embed_texts(texts)
                self.store.add(chunks, embeddings)

            state[rel_path] = file_hash
            self._save_state(state)

    def rebuild(self, doc_dir: str, **kwargs) -> None:
        self.store.delete([])
        if self.state_file.exists():
            self.state_file.unlink()
        self.build(doc_dir, **kwargs)

    def _load_state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {}

    def _save_state(self, state: dict) -> None:
        self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _file_hash(path: Path) -> str:
        return hashlib.md5(path.read_bytes()).hexdigest()
