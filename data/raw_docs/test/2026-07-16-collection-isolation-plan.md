# 分集合部门隔离 + 文档密级 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现部门级物理隔离（分集合）和部门内文档密级控制（L1/L2/L3）

**Architecture:** 
- 索引时：`/rag/index` 新增 `department` 和 `doc_level` 参数，按 department 写入不同 Qdrant 集合
- 检索时：新增 `MultiStoreRetriever` 同时搜索部门集合 + 公共集合，RRF 融合；密级通过 `_build_filters` 过滤
- 用户级别：SSO JWT 中 roles 包含 admin → L3，否则 L2

**Tech Stack:** Python, FastAPI, Qdrant, PyJWT

---

## 文件改动总览

| 文件 | 改动 |
|------|------|
| `src/super_agent/knowledge/models.py` | `UserContext` 新增 `doc_level`；`MetadataSchema` 新增 `doc_level` |
| `src/super_agent/knowledge/metadata.py` | `build_metadata` 新增 `doc_level` 参数，移除 `department` 自动推断 |
| `src/super_agent/knowledge/chunkers/semantic.py` | `_make_chunk` 传递 `doc_level` |
| `src/super_agent/knowledge/retriever.py` | 新增 `MultiStoreRetriever` 类；`_build_filters` 增加密级过滤，移除 department 过滤 |
| `src/super_agent/api/sso.py` | 解析用户 `doc_level` 注入 `UserContext` |
| `src/super_agent/main.py` | `rag_index` 新增 `department`、`doc_level` 参数；`_build_retriever` 改为多 store 策略 |

---

### Task 1: Models — 新增 doc_level 字段

**Files:**
- Modify: `src/super_agent/knowledge/models.py`

- [ ] **Step 1: MetadataSchema 新增 doc_level**

在 `MetadataSchema` 中新增字段：

```python
@dataclass
class MetadataSchema:
    doc_source: str = "local_file"
    doc_type: str = "runbook"
    department: str = ""
    topic_tags: list[str] = field(default_factory=list)
    system_name: str = ""
    severity: str = "normal"
    created_at: str = ""
    updated_at: str = ""
    chunk_type: str = "text"
    parent_chunk_id: str = ""
    page_numbers: list[int] = field(default_factory=list)
    heading_path: str = ""
    doc_version: str = ""
    doc_level: str = "L1"              # ← 新增
    allowed_roles: list[str] = field(default_factory=list)
    allowed_users: list[str] = field(default_factory=list)
    permission_scope: str = "public"
    expiry_date: str = ""
    doc_status: str = "active"
```

- [ ] **Step 2: UserContext 新增 doc_level**

```python
@dataclass
class UserContext:
    user_id: str = ""
    roles: list[str] = field(default_factory=list)
    department: str = ""
    tenant_id: str = ""
    doc_level: str = "L2"              # ← 新增，默认 L2
```

---

### Task 2: Metadata Builder — 添加 doc_level，移除 department 自动推断

**Files:**
- Modify: `src/super_agent/knowledge/metadata.py`

- [ ] **Step 1: 修改 build_metadata 签名和逻辑**

```python
def build_metadata(
    file_path: str,
    doc_source: str = "local_file",
    doc_type: str = "runbook",
    department: str = "",              # 保留参数但不做自动推断
    manual_tags: list[str] | None = None,
    system_name: str = "",
    severity: str = "normal",
    chunk_type: str = "text",
    page_numbers: list[int] | None = None,
    heading_path: str = "",
    doc_version: str = "",
    doc_level: str = "L1",             # ← 新增
    allowed_roles: list[str] | None = None,
    allowed_users: list[str] | None = None,
    permission_scope: str = "public",
    expiry_date: str = "",
    doc_status: str = "active",
) -> dict:
    tags = resolve_topic_tags(file_path, manual_tags)

    # department 不再从目录路径推断，由调用方显式传入
    # 保留 department 参数是为了兼容旧调用方，默认为空

    return {
        "file_path": file_path,
        "doc_source": doc_source,
        "doc_type": doc_type,
        "department": department,
        "topic_tags": tags,
        "system_name": system_name,
        "severity": severity,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
        "chunk_type": chunk_type,
        "parent_chunk_id": "",
        "page_numbers": page_numbers or [],
        "heading_path": heading_path,
        "doc_version": doc_version,
        "doc_level": doc_level,         # ← 新增
        "allowed_roles": allowed_roles or [],
        "allowed_users": allowed_users or [],
        "permission_scope": permission_scope,
        "expiry_date": expiry_date,
        "doc_status": doc_status,
    }
```

关键变更：
- 新增 `doc_level` 参数，默认 `"L1"`
- 移除 `if not department: department = parts[1]` 自动推断逻辑

---

### Task 3: Chunker — 透传 doc_level

**Files:**
- Modify: `src/super_agent/knowledge/chunkers/semantic.py`

- [ ] **Step 1: _make_chunk 中读取并传递 doc_level**

```python
def _make_chunk(
    self, content: str, heading_chain: str, source: str, doc_meta: dict
) -> Chunk:
    full_text = f"{heading_chain}\n{content}" if heading_chain else content
    manual_tags = doc_meta.get("manual_tags")
    doc_level = doc_meta.get("doc_level", "L1")  # ← 从 doc_meta 读取
    meta = build_metadata(
        file_path=source,
        manual_tags=manual_tags,
        doc_level=doc_level,             # ← 传入
    )
    meta.update({k: v for k, v in doc_meta.items() if k not in ("source", "manual_tags")})
    meta["heading_path"] = heading_chain

    page_nums = doc_meta.get("page_numbers", [])

    return Chunk(
        id=str(uuid.uuid4()),
        content=content,
        heading_chain=heading_chain,
        full_text=full_text,
        metadata=meta,
        page_numbers=page_nums,
    )
```

---

### Task 4: Retriever — 新增 MultiStoreRetriever + 密级过滤

**Files:**
- Modify: `src/super_agent/knowledge/retriever.py`

- [ ] **Step 1: 新增 allowed_levels 工具函数**

```python
def allowed_levels(user_level: str) -> list[str]:
    """用户能看的密级列表：level=N 可看所有 <=N 的级别。"""
    ORDER = {"L1": 1, "L2": 2, "L3": 3}
    max_lv = ORDER.get(user_level, 2)
    return [lv for lv, order in ORDER.items() if order <= max_lv]
```

- [ ] **Step 2: 修改 Retriever._build_filters**

```python
def _build_filters(self, user_filters: dict | None, user: UserContext | None) -> dict | None:
    """Merge user-supplied filters with auto-injected permission/tenant filters."""
    result: dict = {}

    # Document status: always exclude expired/inactive docs
    result["doc_status"] = {"$eq": "active"}

    # Doc level: filter by user's clearance level
    # L3 → L1+L2+L3, L2 → L1+L2, L1 → L1
    if user and user.doc_level:
        result["doc_level"] = {"$in": allowed_levels(user.doc_level)}

    # Merge user-supplied filters (AND logic)
    if user_filters:
        for key, value in user_filters.items():
            result[key] = value

    return result if result else None
```

移除旧逻辑：
- `result["department"] = {"$eq": user.department}` — 不再需要，隔离靠集合
- `result["permission_scope"] = {"$in": [...]}` 和相关角色逻辑 — 简化，后续由密级体系替代

- [ ] **Step 3: 新增 MultiStoreRetriever 类**

```python
class MultiStoreRetriever:
    """检索多个向量存储，将结果通过 RRF 融合后返回。"""

    def __init__(
        self,
        stores: list[BaseVectorStore],
        embedder: BaseEmbedder,
        es_client=None,
    ):
        self.stores = stores
        self.embedder = embedder
        self.es_client = es_client

    @traceable(name="multi_store_retriever.retrieve", run_type="chain")
    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
        user: UserContext | None = None,
    ) -> list[Chunk]:
        merged_filters = self._build_filters(filters, user)
        query_emb = self.embedder.embed_query(query)

        all_results: list[list[SearchResult]] = []
        for store in self.stores:
            vector_results = store.search(query_emb, top_k * 3, merged_filters)
            all_results.append([SearchResult(chunk=c, score=1.0) for c in vector_results])

        # RRF fusion
        if len(all_results) > 1:
            candidates = reciprocal_rank_fusion(*all_results, k=60)
        else:
            candidates = all_results[0] if all_results else []

        # Deduplicate
        candidates = deduplicate_overlaps(candidates)

        return [r.chunk for r in candidates[:top_k]]

    def _build_filters(self, user_filters: dict | None, user: UserContext | None) -> dict | None:
        """与 Retriever._build_filters 相同的过滤逻辑。"""
        result: dict = {}
        result["doc_status"] = {"$eq": "active"}
        if user and user.doc_level:
            result["doc_level"] = {"$in": allowed_levels(user.doc_level)}
        if user_filters:
            for key, value in user_filters.items():
                result[key] = value
        return result if result else None
```

- [ ] **Step 4: 更新 `retriever.py` 的 `__init__.py` 导出**

确保 `__init__.py` 或引用处能访问到 `MultiStoreRetriever`。

---

### Task 5: SSO 中间件 — 解析 doc_level

**Files:**
- Modify: `src/super_agent/api/sso.py`

- [ ] **Step 1: 添加 doc_level 字段到 request.state.user**

在 SSO 中间件的 `dispatch` 方法中找到注入 `UserContext` 的位置：

```python
# 计算用户密级
doc_level = "L3" if "admin" in roles else "L2"

request.state.user = UserContext(
    user_id=user_id,
    department=department,
    tenant_id="",
    roles=roles,
    doc_level=doc_level,  # ← 新增
)
```

- [ ] **Step 2: /auth/me 返回 doc_level**

```python
return {
    "user_id": str(claims.get("user_id", "")),
    "username": str(claims.get("username", "")),
    "display_name": str(claims.get("username", "")),
    "roles": roles,
    "department": str(claims.get("dept_id", "")),
    "doc_level": "L3" if "admin" in roles else "L2",  # ← 新增
}
```

---

### Task 6: 索引 API — 新增 department 和 doc_level 参数

**Files:**
- Modify: `src/super_agent/main.py`（rag_index 函数）

- [ ] **Step 1: 更新 rag_index 函数签名和参数处理**

```python
@app.post("/rag/index")
async def rag_index(
    doc_dir: str = "data/raw_docs",
    force: bool = False,
    tenant_id: str = "",
    use_llm: bool = False,
    department: str = "",        # ← 新增
    doc_level: str = "L1",       # ← 新增
):
    """构建 / 重建知识库索引。

    新增参数:
        department: str (默认 "")  — 部门 ID。空=公共集合，有值=对应部门集合
        doc_level: str (默认 "L1") — 文档密级 L1/L2/L3
    """
    from super_agent.knowledge.indexer import Indexer
    from super_agent.knowledge.stores import get_store
    from super_agent.knowledge.embedders import get_embedder
    from super_agent.knowledge.chunkers import SemanticChunker

    if use_llm:
        from super_agent.knowledge.chunkers.llm_assisted import LLMAssistedChunker
        chunker = LLMAssistedChunker(use_llm=True)
    else:
        chunker = SemanticChunker()

    # department 优先于 tenant_id
    effective_tenant = department or tenant_id
    store = get_store(tenant_id=effective_tenant)
    embedder = get_embedder()

    # ES BM25 hybrid client
    es_client = None
    if settings.rag.enable_bm25_hybrid:
        try:
            from super_agent.knowledge.es_client import ESClient
            es_client = ESClient()
        except Exception as e:
            logger.warning("ES client init failed, BM25 hybrid disabled: %s", e)

    indexer = Indexer(
        store=store,
        embedder=embedder,
        chunker=chunker,
        tenant_id=effective_tenant,
        es_client=es_client,
        doc_level=doc_level,  # ← 新增：传给 indexer
    )
    if force:
        indexer.rebuild(doc_dir)
    else:
        indexer.build(doc_dir)
    return {"status": "indexed" if not force else "rebuilt", "doc_dir": doc_dir, "total_chunks": store.count()}
```

- [ ] **Step 2: Indexer 类接收 doc_level 并传递给 chunker**

修改 `src/super_agent/knowledge/indexer.py`，在 `__init__` 和 `build` 中接收 `doc_level`：

```python
class Indexer:
    def __init__(
        self,
        store: BaseVectorStore,
        embedder: BaseEmbedder,
        chunker: BaseChunker,
        state_dir: str = "./data/index_state",
        tenant_id: str = "",
        es_client=None,
        doc_level: str = "L1",          # ← 新增
    ):
        self.store = store
        self.embedder = embedder
        self.chunker = chunker
        self.es_client = es_client
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state_file_name = f"index_state_{tenant_id}.json" if tenant_id else "index_state.json"
        self.state_file = self.state_dir / state_file_name
        self.doc_level = doc_level         # ← 新增

    @traceable(name="indexer.build", run_type="chain")
    def build(self, doc_dir: str, file_tags: dict[str, list[str]] | None = None, **kwargs) -> None:
        doc_path = Path(doc_dir)
        state = self._load_state()
        ...

        # 在处理每个文件时：
        for doc in documents:
            doc.metadata["manual_tags"] = merged
            doc.metadata["doc_version"] = new_version
            doc.metadata["doc_level"] = self.doc_level  # ← 新增：将 doc_level 写入 doc metadata
        ...
```

---

### Task 7: _build_retriever — 多 store 策略

**Files:**
- Modify: `src/super_agent/main.py`（_build_retriever 函数）

- [ ] **Step 1: 重写 _build_retriever**

```python
def _build_retriever(user: UserContext):
    """Build retriever based on user context.

    - 无部门 → 只查公共集合 super_agent_docs
    - 普通用户 → 查部门集合 + 公共集合（双路 RRF）
    - 超管 → 查所有部门集合 + 公共集合
    """
    from super_agent.knowledge.retriever import Retriever, MultiStoreRetriever
    from super_agent.knowledge.stores import get_store, get_all_tenant_stores
    from super_agent.knowledge.embedders import get_embedder

    embedder = get_embedder()

    es_client = None
    if settings.rag.enable_bm25_hybrid:
        try:
            from super_agent.knowledge.es_client import ESClient
            es_client = ESClient()
        except Exception as e:
            logger.warning("ES client init failed, BM25 hybrid disabled: %s", e)

    public_store = get_store()  # super_agent_docs

    if not user.department:
        # 无部门 → 只看公共
        return Retriever(store=public_store, embedder=embedder, es_client=es_client)

    if "admin" in user.roles:
        # 超管 → 查所有
        dept_stores = get_all_tenant_stores()
        all_stores = [public_store] + dept_stores
        return MultiStoreRetriever(stores=all_stores, embedder=embedder, es_client=es_client)

    # 普通用户 → 查自己部门 + 公共
    dept_store = get_store(tenant_id=user.department)
    return MultiStoreRetriever(stores=[dept_store, public_store], embedder=embedder, es_client=es_client)
```

---

## 执行顺序

1. **Task 1** — Models: 新增 doc_level 字段
2. **Task 2** — Metadata builder: 添加 doc_level，移除 department 自动推断
3. **Task 3** — Chunker: 透传 doc_level
4. **Task 4** — Retriever: MultiStoreRetriever + 密级过滤
5. **Task 5** — SSO 中间件: 解析 doc_level
6. **Task 6** — 索引 API: 新增 department/doc_level 参数
7. **Task 7** — _build_retriever: 多 store 策略

每个任务可独立验证，按顺序依赖。Task 6 和 7 都改 `main.py`，注意不要冲突。
