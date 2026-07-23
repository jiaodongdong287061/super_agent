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
    # 将多个空白符合并为一个空格
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


class APIEmbedder(BaseEmbedder):
    def __init__(self):
        cfg = settings.embedding

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        cfg = settings.embedding
        all_embeddings: list[list[float]] = []
        max_input_chars = 4000  # ~4000 Chinese tokens, safe for most embedding APIs

        masked_key = cfg.api_key[:8] + "..." if cfg.api_key else ""

        for i, text in enumerate(texts):
            if not text or not text.strip():
                logger.warning("Skipping empty text at index %d", i)
                continue

            cleaned = sanitize_text(text)
            if not cleaned:
                logger.warning("Skipping text at index %d after sanitization (became empty)", i)
                continue

            # 截断超长输入，避免 API token 限制
            if len(cleaned) > max_input_chars:
                logger.warning(
                    "Truncating text at index %d from %d to %d chars",
                    i, len(cleaned), max_input_chars,
                )
                cleaned = cleaned[:max_input_chars]

            payload = {"model": cfg.api_model, "input": cleaned}
            logger.info(
                "Embedding request [%d/%d]: model=%s key=%s input_len=%d preview=%s",
                i + 1, len(texts), cfg.api_model, masked_key, len(cleaned), cleaned[:80],
            )
            resp = httpx.post(
                f"{cfg.api_url}",
                json=payload,
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                timeout=60.0,
            )
            if resp.status_code >= 400:
                logger.error(
                    "Embedding API error [%s] at index %d: %s\n  preview=%s",
                    resp.status_code,
                    i,
                    resp.text,
                    cleaned[:200],
                )
                raise RuntimeError(
                    f"Embedding API returned {resp.status_code} at index {i}: {resp.text}"
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
