# 多 Collection + Fan-out 多租户隔离方案

## Context

当前所有租户数据混在同一个向量集合（`super_agent_docs`）里，通过 metadata filter 区分。
这会带来性能和安全隐患（filter 写漏 = 数据泄露）。目标是用 **每个租户独立集合** + **admin 跨租户 fan-out 查询** 替代。

数据只在各租户集合中存在，不留全局副本，安全性更高。

---

## 架构概览

```
写路径（不变）：
  索引文档 → super_agent_docs_{tenant_id}  （每个租户独立集合）

读路径（分两种）：
  普通用户 ─→ store.search()  → 只查自己租户的集合（1 次查询）
  Admin    ─→ FanOutRetriever → 并行查所有 tenant_* 集合 → RRF 合并 → Top-K
```

租户集合自动发现：通过向量库的 `list_collections()` API，扫描以 `{base_name}_` 开头的集合。

---

## 改动清单

### 1. `models.py` — UserContext 增加 tenant_id

`src/super_agent/knowledge/models.py:72-75`

```python
@dataclass
class UserContext:
    user_id: str = ""
    roles: list[str] = field(default_factory=list)
    department: str = ""
    tenant_id: str = ""          # 新增：租户标识，空 = admin 跨租户查询
```

### 2. 三个 Store 实现 — 构造时接受 tenant_id，派生集合名

**`chroma_store.py:13-14`**：
```python
def __init__(self, persist_dir: str | None = None, tenant_id: str = ""):
    cfg = settings.vector_store
    col_name = cfg.chroma_collection  # "super_agent_docs"
    if tenant_id:
        col_name = f"{col_name}_{tenant_id}"
    self.collection = self.client.get_or_create_collection(name=col_name, ...)
```

**`milvus_store.py:15`** 和 **`qdrant_store.py:18`** 同理。

### 3. `stores/__init__.py` — get_store() 透传 tenant_id

`src/super_agent/knowledge/stores/__init__.py:4-18`

```python
def get_store(provider: str | None = None, tenant_id: str = "") -> BaseVectorStore:
    ...
    if provider == "chroma":
        return ChromaStore(tenant_id=tenant_id)
    elif provider == "milvus":
        return MilvusStore(tenant_id=tenant_id)
    elif provider == "qdrant":
        return QdrantStore(tenant_id=tenant_id)
```

### 4. 新增 `FanOutRetriever` — admin 跨租户检索

新建 `src/super_agent/knowledge/fanout_retriever.py`

```python
class FanOutRetriever:
    """并行查询所有租户集合，RRF 合并结果。"""

    def __init__(self, stores: list[BaseVectorStore], embedder: BaseEmbedder):
        self.stores = stores
        self.embedder = embedder

    def retrieve(
        self, query: str, top_k: int = 5, filters: dict | None = None
    ) -> list[Chunk]:
        # 1. 并行查询所有 store
        # 2. 复用 retriever.py 已有的 _reciprocal_rank_fusion 合并
        # 3. 复用 retriever.py 已有的 _deduplicate_overlaps 去重
        # 4. 返回 Top-K
```

关键实现细节：
- 使用 `concurrent.futures.ThreadPoolExecutor` 并行查询（store 操作是同步 IO）
- 复用 `retriever.py:74-95` 的 RRF 算法
- 复用 `retriever.py:97-103` 的去重逻辑

**集合发现机制**（存放在 `stores/__init__.py` 或 `fanout_retriever.py`）：

```python
def discover_tenant_collections(base_name: str, provider: str) -> list[str]:
    """通过向量库 API 自动发现所有 tenant 集合名。"""
    ...
    # Chroma: client.list_collections() → 过滤以 base_name + "_" 开头的
    # Qdrant: client.get_collections().collections → 过滤
    # Milvus: client.list_collections() → 过滤
```

### 5. `main.py` — API 端点区分普通用户和 admin

**`/rag/query`**：
```python
if req.user.tenant_id:
    # 普通用户：单集合查询
    store = get_store(tenant_id=req.user.tenant_id)
    retriever = Retriever(store=store, embedder=embedder)
else:
    # admin 或无 tenant_id：fan-out 所有集合
    stores = get_all_tenant_stores()  # 自动发现 + 创建
    retriever = FanOutRetriever(stores=stores, embedder=embedder)
```

**`/rag/index`**：新增可选参数 `tenant_id`
```python
async def rag_index(doc_dir: str = "data/raw_docs", force: bool = False, tenant_id: str = ""):
    store = get_store(tenant_id=tenant_id)
```

**`/rag/delete`**、**`/rag/doc/status`**、**`/rag/doc/list`** 同理。

### 6. `indexer.py` — 索引状态文件按租户隔离（可选）

`src/super_agent/knowledge/indexer.py:26-27`

```python
# 当前：data/index_state/index_state.json
# 改为：data/index_state/index_state_{tenant_id}.json
```

### 7. Config — 不新增必需配置，仅加一个可选默认值

`src/super_agent/config.py:64-78` — 集合名保持 `"super_agent_docs"` 不变，靠代码逻辑追加 `_{tenant_id}`。

---

## 影响范围

| 文件 | 改动 |
|------|------|
| `models.py` | `UserContext` 加 1 行 `tenant_id` |
| `stores/__init__.py` | `get_store()` 加 `tenant_id` 参数 + 新增 `discover_tenant_collections()` |
| `chroma_store.py` | 构造器加 `tenant_id`，集合名派生 |
| `milvus_store.py` | 同上 |
| `qdrant_store.py` | 同上 |
| `stores/base.py` | 无变化 |
| `fanout_retriever.py` | **新建** — Fan-out 检索 + RRF 合并 |
| `retriever.py` | **无变化** — RRF 和去重逻辑改为可以被 fanout_retriever 引用 |
| `indexer.py` | 索引状态文件按租户隔离（可选） |
| `main.py` | 各端点透传 `tenant_id`；query 端点判断是否走 fan-out |
| `config.py` | 无变化 |

---

## 安全性分析

| 场景 | 保障 |
|------|------|
| 普通用户查自己租户 | ✅ 集合天然隔离，不存在 filter 漏写问题 |
| 普通用户猜其他租户集合名 | ✅ `tenant_id` 来自 `UserContext`，无认证绕过则不可能 |
| Admin 跨租户搜索 | ✅ 只读 fan-out，无法跨租户写入 |
| 新租户自动接入 | ✅ 写时自动创建集合，读时自动发现 |

---

## 验证方案

1. **向后兼容**：不传 `tenant_id` 建索引 + 查询 → 结果与改前一致
2. **租户隔离**：`tenant_id=A` 建索引 + `tenant_id=A` 查询 → 有结果；`tenant_id=B` 查询 → 无结果
3. **Admin fan-out**：`tenant_id=""` + admin 角色 → 返回所有租户的合并结果
4. **集合自动发现**：新建一个 `super_agent_docs_c` 集合后，fan-out 自动包含它
