from super_agent.knowledge.stores.base import BaseVectorStore


def get_store(provider: str | None = None, tenant_id: str = "") -> BaseVectorStore:
    from super_agent.config import settings

    provider = provider or settings.vector_store.provider
    if provider == "chroma":
        from super_agent.knowledge.stores.chroma_store import ChromaStore
        return ChromaStore(tenant_id=tenant_id)
    elif provider == "milvus":
        from super_agent.knowledge.stores.milvus_store import MilvusStore
        return MilvusStore(tenant_id=tenant_id)
    elif provider == "qdrant":
        from super_agent.knowledge.stores.qdrant_store import QdrantStore
        return QdrantStore(tenant_id=tenant_id)
    else:
        raise ValueError(f"Unknown vector store provider: {provider}")


def discover_tenant_collections() -> list[str]:
    """Auto-discover all tenant-specific collections via vector store API.

    Returns collection names that start with the base collection name + "_".
    The base collection itself is excluded from the result.
    """
    from super_agent.config import settings

    cfg = settings.vector_store
    base_name = "super_agent_docs"
    prefix = f"{base_name}_"

    if cfg.provider == "chroma":
        import chromadb
        client = chromadb.PersistentClient(path=cfg.chroma_persist_dir)
        return [c.name for c in client.list_collections() if c.name.startswith(prefix)]

    elif cfg.provider == "milvus":
        from pymilvus import MilvusClient
        client = MilvusClient(uri=f"http://{cfg.milvus_host}:{cfg.milvus_port}")
        return [c for c in client.list_collections() if c.startswith(prefix)]

    elif cfg.provider == "qdrant":
        from qdrant_client import QdrantClient
        client_kwargs: dict = {"url": cfg.qdrant_url, "prefer_grpc": False}
        if cfg.qdrant_api_key:
            client_kwargs["api_key"] = cfg.qdrant_api_key
        client = QdrantClient(**client_kwargs)
        return [c.name for c in client.get_collections().collections if c.name.startswith(prefix)]

    return []


def get_all_tenant_stores() -> list[BaseVectorStore]:
    """Create store instances for all discovered tenant collections."""
    collections = discover_tenant_collections()
    base_name = "super_agent_docs"
    stores = []
    for col_name in collections:
        tenant_id = col_name[len(base_name) + 1:]
        stores.append(get_store(tenant_id=tenant_id))
    return stores
