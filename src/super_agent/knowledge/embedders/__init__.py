from super_agent.knowledge.embedders.base import BaseEmbedder


def get_embedder(provider: str | None = None) -> BaseEmbedder:
    from super_agent.config import settings

    provider = provider or settings.embedding.provider
    if provider == "api":
        from super_agent.knowledge.embedders.api import APIEmbedder
        return APIEmbedder()
    else:
        raise ValueError(f"Unknown embedder provider: {provider}")
