from __future__ import annotations

import logging
import re

import httpx

from super_agent.config import settings
from super_agent.knowledge.embedders.base import BaseEmbedder

logger = logging.getLogger(__name__)

# 清理 PDF 提取文本中的控制字符（保留换行、制表符等空白字符）
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str) -> str:
    """去除可能导致 API 拒绝的控制字符。"""
    text = _CONTROL_CHAR_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


class APIEmbedder(BaseEmbedder):
    def __init__(self):
        cfg = settings.embedding

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        cfg = settings.embedding
        all_embeddings: list[list[float]] = []
        max_input_chars = 4000
        batch_size = cfg.api_batch_size

        masked_key = cfg.api_key[:8] + "..." if cfg.api_key else ""

        # 按 batch 分组处理
        for batch_idx in range(0, len(texts), batch_size):
            batch_texts = texts[batch_idx : batch_idx + batch_size]
            cleaned_batch = []
            for t in batch_texts:
                if not t or not t.strip():
                    continue
                cleaned = sanitize_text(t)
                if not cleaned:
                    continue
                if len(cleaned) > max_input_chars:
                    cleaned = cleaned[:max_input_chars]
                cleaned_batch.append(cleaned)

            if not cleaned_batch:
                continue

            payload = {"model": cfg.api_model, "input": cleaned_batch}
            logger.info(
                "Embedding batch [%d/%d]: size=%d model=%s",
                batch_idx // batch_size + 1,
                (len(texts) + batch_size - 1) // batch_size,
                len(cleaned_batch),
                cfg.api_model,
            )

            headers = {}
            if cfg.api_key:
                headers["Authorization"] = f"Bearer {cfg.api_key}"

            resp = httpx.post(
                f"{cfg.api_url}",
                json=payload,
                headers=headers,
                timeout=120.0,
            )
            if resp.status_code >= 400:
                logger.error(
                    "Embedding API error [%s] at batch %d: %s",
                    resp.status_code,
                    batch_idx // batch_size,
                    resp.text[:200],
                )
                raise RuntimeError(
                    f"Embedding API returned {resp.status_code} at batch {batch_idx // batch_size}: {resp.text[:200]}"
                )
            resp.raise_for_status()
            data = resp.json()["data"]
            embeddings = [d["embedding"] for d in sorted(data, key=lambda x: x["index"])]
            all_embeddings.extend(embeddings)

        if not all_embeddings:
            raise RuntimeError("Embedding returned empty result, check query content")
        return all_embeddings

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    @property
    def dimension(self) -> int:
        sample = self.embed_query("维度探测")
        return len(sample)
