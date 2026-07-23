from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from langsmith import traceable

from super_agent.knowledge.stores.base import BaseVectorStore
from super_agent.knowledge.embedders.base import BaseEmbedder
from super_agent.knowledge.chunkers.base import BaseChunker
from super_agent.knowledge.loaders import get_loader, supported_extensions
from super_agent.knowledge.tags import parse_tags_yaml, match_file_tags
from super_agent.config import settings

logger = logging.getLogger(__name__)


class Indexer:
    def __init__(
        self,
        store: BaseVectorStore,
        embedder: BaseEmbedder,
        chunker: BaseChunker,
        state_dir: str = "./data/index_state",
        tenant_id: str = "",
        es_client=None,  # ESClient | None: BM25 混合检索用
        doc_level: str = "L1",
    ):
        self.store = store
        self.embedder = embedder
        self.chunker = chunker
        self.es_client = es_client
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state_file_name = f"index_state_{tenant_id}.json" if tenant_id else "index_state.json"
        self.state_file = self.state_dir / state_file_name
        self.doc_level = doc_level

    @traceable(name="indexer.build", run_type="chain")
    def build(self, doc_dir: str, file_tags: dict[str, list[str]] | None = None, **kwargs) -> None:
        doc_path = Path(doc_dir)
        state = self._load_state()

        tags_yaml_path = doc_path / "tags.yaml"
        yaml_tags = parse_tags_yaml(tags_yaml_path)

        current_files: set[str] = set()
        seen_paths: set[str] = set()

        for fp in doc_path.rglob("*"):
            if not fp.is_file() or fp.suffix.lower() not in supported_extensions():
                continue
            if fp.name == "tags.yaml":
                continue

            file_hash = self._file_hash(fp)
            rel_path = str(fp)
            current_files.add(rel_path)
            seen_paths.add(rel_path)

            old_state = state.get(rel_path)
            if isinstance(old_state, dict) and old_state.get("hash") == file_hash:
                continue

            loader = get_loader(fp.suffix.lower())
            documents = loader.load(str(fp))

            manual_tags = file_tags.get(str(fp), []) if file_tags else []
            norm_path = str(fp).replace("\\", "/")
            yaml_matched = match_file_tags(norm_path, yaml_tags)
            merged = manual_tags + [t for t in yaml_matched if t not in manual_tags]

            # LLM 自动打标：当无手动标注且开启配置时，用 LLM 生成 topic_tags
            llm_tags = _generate_llm_tags(documents, file_path=str(fp)) if not merged and settings.rag.enable_llm_tagging else None
            if llm_tags:
                merged = llm_tags

            # 版本追踪：文件变更时递增版本号
            old_version = old_state.get("version", "0") if isinstance(old_state, dict) else "0"
            new_version = str(int(old_version) + 1) if isinstance(old_state, dict) and old_state.get("hash") != file_hash else old_version

            for doc in documents:
                doc.metadata["manual_tags"] = merged
                doc.metadata["doc_version"] = new_version
                doc.metadata["doc_level"] = self.doc_level

            chunks = self.chunker.chunk(documents, **kwargs)

            if chunks:
                texts = [c.full_text for c in chunks]
                embeddings = self.embedder.embed_texts(texts)
                self.store.add(chunks, embeddings)
                if self.es_client:
                    self.es_client.add(chunks)

            state[rel_path] = {
                "hash": file_hash,
                "version": new_version,
                "last_indexed": datetime.now().isoformat(),
                "chunk_ids": [c.id for c in chunks],
            }
            self._save_state(state)  # 每文件保存，防止中途中断丢失状态

        # 清理已删除的文件：state 中存在但磁盘上已删除的文件
        stale_paths = [p for p in state if p not in current_files]
        if stale_paths:
            stale_chunk_ids: list[str] = []
            for p in stale_paths:
                stale_chunk_ids.extend(state[p].get("chunk_ids", []))
                # 同时清理 ES 索引
                if self.es_client:
                    self.es_client.delete_by_file_path(p)
                del state[p]
            if stale_chunk_ids:
                self.store.delete(stale_chunk_ids)
                logger.info(
                    "Removed %d stale chunk(s) from %d deleted file(s)",
                    len(stale_chunk_ids), len(stale_paths),
                )
            self._save_state(state)

    def rebuild(self, doc_dir: str, **kwargs) -> None:
        self.store.delete([])
        if self.state_file.exists():
            self.state_file.unlink()
        self.build(doc_dir, **kwargs)

    def get_document_status(self, doc_path: str) -> dict | None:
        state = self._load_state()
        norm = str(Path(doc_path))
        entry = state.get(norm)
        if entry is None:
            return None
        return {
            "file_path": norm,
            "version": entry["version"],
            "file_hash": entry["hash"],
            "last_indexed": entry["last_indexed"],
        }

    def list_documents(self) -> list[dict]:
        state = self._load_state()
        return [
            {
                "file_path": path,
                "version": info["version"],
                "last_indexed": info["last_indexed"],
            }
            for path, info in state.items()
        ]

    def _load_state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {}

    def _save_state(self, state: dict) -> None:
        self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _file_hash(path: Path) -> str:
        return hashlib.md5(path.read_bytes()).hexdigest()


def _generate_llm_tags(documents: list, file_path: str) -> list[str] | None:
    """调用 LLM 为文档生成 topic_tags。"""
    try:
        from super_agent.knowledge.llm_client import LLMClient
        from super_agent.prompts import get_prompt

        # 取文档内容前 1500 字作为分析素材
        content = "\n".join(doc.page_content[:1500] for doc in documents if doc.page_content)
        if not content:
            return None

        prompt = get_prompt("topic_tagging", content=content)
        data = LLMClient().chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=128,
        )
        text = data["choices"][0]["message"]["content"].strip()
        tags = [t.strip() for t in text.split(",") if t.strip()]
        if tags:
            logger.info("LLM-tagged %s → %s", file_path, tags)
            return tags
    except Exception as e:
        logger.warning("LLM tagging failed for %s: %s", file_path, e)
    return None
