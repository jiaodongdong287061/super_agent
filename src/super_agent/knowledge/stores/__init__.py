from super_agent.knowledge.stores.base import BaseVectorStore


def get_store(provider: str | None = None) -> BaseVectorStore:
    from super_agent.config import settings

    provider = provider or settings.vector_store.provider
    if provider == "chroma":
        from super_agent.knowledge.stores.chroma_store import ChromaStore
        return ChromaStore()
    elif provider == "milvus":
        from super_agent.knowledge.stores.milvus_store import MilvusStore
        return MilvusStore()
    elif provider == "qdrant":
        from super_agent.knowledge.stores.qdrant_store import QdrantStore
        return QdrantStore()
    else:
        raise ValueError(f"Unknown vector store provider: {provider}")
