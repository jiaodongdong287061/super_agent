from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
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

        if doc_path.is_file():
            self._build_single_file(doc_path, **kwargs)
        else:
            self._build_directory(doc_path, file_tags, **kwargs)

    def _build_single_file(self, file_path: Path, **kwargs) -> None:
        """索引单个文件。"""
        state = self._load_state()

        if file_path.suffix.lower() not in supported_extensions():
            logger.warning("Unsupported file extension: %s", file_path.suffix)
            return

        file_hash = self._file_hash(file_path)
        rel_path = str(file_path)

        old_state = state.get(rel_path)
        if isinstance(old_state, dict) and old_state.get("hash") == file_hash:
            logger.info("File unchanged, skipping: %s", rel_path)
            return

        loader = get_loader(file_path.suffix.lower())
        documents = loader.load(str(file_path))

        old_version = old_state.get("version", "0") if isinstance(old_state, dict) else "0"
        new_version = str(int(old_version) + 1)

        for doc in documents:
            doc.metadata["doc_version"] = new_version
            doc.metadata["doc_level"] = self.doc_level

        chunks = self.chunker.chunk(documents, **kwargs)
        logger.info("File %s → %d chunks", rel_path, len(chunks))
        self._attribute_page_numbers(chunks, documents)
        self._index_chunks(chunks, rel_path)

        state[rel_path] = {
            "hash": file_hash,
            "version": new_version,
            "last_indexed": datetime.now().isoformat(),
            "chunk_ids": [c.id for c in chunks],
        }
        self._save_state(state)

    def _build_directory(self, doc_path: Path, file_tags: dict[str, list[str]] | None = None, **kwargs) -> None:
        state = self._load_state()

        tags_yaml_path = doc_path / "tags.yaml"
        yaml_tags = parse_tags_yaml(tags_yaml_path)

        current_files: set[str] = set()

        for fp in doc_path.rglob("*"):
            if not fp.is_file() or fp.suffix.lower() not in supported_extensions():
                continue
            if fp.name == "tags.yaml":
                continue

            file_hash = self._file_hash(fp)
            rel_path = str(fp)
            current_files.add(rel_path)

            old_state = state.get(rel_path)
            if isinstance(old_state, dict) and old_state.get("hash") == file_hash:
                continue

            loader = get_loader(fp.suffix.lower())
            documents = loader.load(str(fp))

            manual_tags = file_tags.get(str(fp), []) if file_tags else []
            norm_path = str(fp).replace("\\", "/")
            yaml_matched = match_file_tags(norm_path, yaml_tags)
            merged = manual_tags + [t for t in yaml_matched if t not in manual_tags]

            # LLM 自动打标
            llm_tags = _generate_llm_tags(documents, file_path=str(fp)) if not merged and settings.rag.enable_llm_tagging else None
            if llm_tags:
                merged = llm_tags

            # 版本追踪
            old_version = old_state.get("version", "0") if isinstance(old_state, dict) else "0"
            new_version = str(int(old_version) + 1) if isinstance(old_state, dict) and old_state.get("hash") != file_hash else old_version

            for doc in documents:
                doc.metadata["manual_tags"] = merged
                doc.metadata["doc_version"] = new_version
                doc.metadata["doc_level"] = self.doc_level

            chunks = self.chunker.chunk(documents, **kwargs)
            logger.info("File %s → %d chunks", rel_path, len(chunks))

            self._attribute_page_numbers(chunks, documents)
            self._index_chunks(chunks, rel_path)

            state[rel_path] = {
                "hash": file_hash,
                "version": new_version,
                "last_indexed": datetime.now().isoformat(),
                "chunk_ids": [c.id for c in chunks],
            }
            self._save_state(state)

        # 清理已删除的文件
        stale_paths = [p for p in state if p not in current_files]
        if stale_paths:
            stale_chunk_ids: list[str] = []
            for p in stale_paths:
                stale_chunk_ids.extend(state[p].get("chunk_ids", []))
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

    def _index_chunks(self, chunks: list, rel_path: str) -> None:
        """并发 embedding + 写库。"""
        if not chunks:
            return
        batch_size = 64
        batches = [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]

        def _process_batch(batch_idx: int, batch_chunks: list) -> int:
            texts = [c.full_text for c in batch_chunks]
            embeddings = self.embedder.embed_texts(texts)
            self.store.add(batch_chunks, embeddings)
            if self.es_client:
                self.es_client.add(batch_chunks)
            return batch_idx

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(_process_batch, i, b): i
                for i, b in enumerate(batches)
            }
            for future in as_completed(futures):
                idx = futures[future]
                future.result()
                logger.info("Indexed batch [%d/%d] for %s", idx + 1, len(batches), rel_path)

    def rebuild(self, doc_dir: str, **kwargs) -> None:
        self.store.clear()
        if self.es_client:
            self.es_client.clear()
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
    def _attribute_page_numbers(chunks: list, documents: list) -> None:
        """用 chunk.char_start 对比 page_end_offsets 反算该 chunk 的实际页码。"""
        for chunk in chunks:
            offsets = chunk.metadata.get("page_end_offsets")
            page_nums = chunk.metadata.get("page_numbers")
            if not offsets or not page_nums:
                continue
            char_end = chunk.char_start + len(chunk.content)
            pages = []
            prev = 0
            for i, end in enumerate(offsets):
                if chunk.char_start < end and char_end > prev:
                    pages.append(page_nums[i])
                prev = end
            if pages:
                chunk.page_numbers = pages

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
