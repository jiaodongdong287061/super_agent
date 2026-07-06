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
        batch_size = cfg.api_batch_size or 64
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            resp = httpx.post(
                f"{cfg.api_url}",
                json={"model": cfg.api_model, "input": batch},
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                timeout=60.0,
            )
            if resp.status_code >= 400:
                logger.error(
                    "Embedding API error [%s]: %s",
                    resp.status_code,
                    resp.text,
                )
            resp.raise_for_status()
            data = resp.json()["data"]
            embeddings = [d["embedding"] for d in sorted(data, key=lambda x: x["index"])]
            all_embeddings.extend(embeddings)

        return all_embeddings

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    @property
    def dimension(self) -> int:
        sample = self.embed_query("dimension probe")
        return len(sample)
