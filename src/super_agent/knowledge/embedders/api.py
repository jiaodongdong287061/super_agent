from __future__ import annotations

import logging

import httpx

from super_agent.config import settings
from super_agent.knowledge.embedders.base import BaseEmbedder

logger = logging.getLogger(__name__)


class APIEmbedder(BaseEmbedder):
    def __init__(self):
        cfg = settings.embedding

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        cfg = settings.embedding
        all_embeddings: list[list[float]] = []

        for text in texts:
            if not text or not text.strip():
                logger.warning("Skipping empty text in embedding request")
                continue
            payload = {"model": cfg.api_model, "input": text}
            logger.info("Embedding request: model=%s input_len=%d preview=%s",
                        cfg.api_model, len(text), text[:80])
            resp = httpx.post(
                f"{cfg.api_url}",
                json=payload,
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                timeout=60.0,
            )
            if resp.status_code >= 400:
                logger.error(
                    "Embedding API error [%s]: %s",
                    resp.status_code,
                    resp.text,
                )
                raise RuntimeError(f"Embedding API returned {resp.status_code}: {resp.text}")
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
        sample = self.embed_query("dimension probe")
        return len(sample)
