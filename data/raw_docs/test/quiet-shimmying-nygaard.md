# 删除向量库接口

## Context

当前项目中所有 VectorStore（Chroma/Milvus/Qdrant）已实现 `delete(chunk_ids)` 方法，
但没有 `clear()`（全量清空），也没有暴露给 API。用户需要一个 HTTP 接口来删除向量数据。

## 改动清单

### 1. BaseVectorStore 新增 clear() 抽象方法

**文件:** `src/super_agent/knowledge/stores/base.py`

- 新增 `@abstractmethod def clear(self) -> None: ...`
- 语义：清空当前集合中的所有向量数据

### 2. 三个 Store 实现 clear()

**文件:**
- `src/super_agent/knowledge/stores/chroma_store.py`
- `src/super_agent/knowledge/stores/milvus_store.py`
- `src/super_agent/knowledge/stores/qdrant_store.py`

实现方式统一为：**删除集合 → 重建集合**（保留同名集合，恢复空状态）

| Store | 实现 |
|-------|------|
| ChromaStore | `self.client.delete_collection(self.collection_name)` → 重新 `get_or_create_collection` |
| MilvusStore | `self.client.drop_collection(self.collection_name)` → 重新创建 collection（需带上 schema） |
| QdrantStore | `self.client.delete_collection(self.collection_name)` → 调用 `self._ensure_collection()` 重建 |

### 3. API 端点

**文件:** `src/super_agent/main.py`

新增 `POST /rag/delete`，请求体支持两种模式：

```python
class DeleteRequest(BaseModel):
    chunk_ids: list[str] | None = None  # None 或 [] 表示清空全部
```

| 请求 | 行为 |
|------|------|
| `{"chunk_ids": ["id1", "id2"]}` | 删除指定 chunk |
| `{}` 或 `{"chunk_ids": []}` | 清空全部 |

响应：

```python
class DeleteResponse(BaseModel):
    status: str
    deleted_count: int = 0
```

逻辑：
- `chunk_ids` 有值且非空 → 调用 `store.delete(chunk_ids)`
- `chunk_ids` 为 None 或空列表 → 调用 `store.clear()`

### 4. 测试

**新增:** `tests/integration/test_rag_delete.py`

- `test_delete_by_chunk_ids`: 选中删除指定 IDs
- `test_clear_all`: 清空全部
- `test_delete_empty_ids_is_clear`: 空列表等于清空

## 验证

```bash
uv run pytest tests/unit/test_stores.py tests/integration/ -v
```
