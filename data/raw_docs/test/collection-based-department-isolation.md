# 基于分集合的部门数据隔离设计方案

> 实现不同部门的知识库数据物理隔离，公共文档跨部门可见，部门内部按密级分级

## 1. 背景

SSO 认证已能获取用户的 `user_id`、`department`（部门 ID）、`roles`（角色列表）。目前所有文档索引到同一个 Qdrant 集合 `super_agent_docs`。

**决策结论**：
- 部门隔离：采用分集合方案（物理隔离）
- 部门内部：采用文档密级方案（L1/L2/L3，metadata 过滤）

## 2. 集合命名规范

| 集合 | 用途 |
|------|------|
| `super_agent_docs` | 公共文档，全公司可见 |
| `super_agent_docs_103` | 部门 103 的文档 |
| `super_agent_docs_201` | 部门 201 的文档 |
| `super_agent_docs_{dept_id}` | 其他部门 |

## 3. 文档密级体系

### 3.1 密级定义

| 级别 | 标签 | 含义 | 默认 |
|------|------|------|------|
| L1 | 公开 | 部门内全员可见 | ✓ |
| L2 | 内部 | 部门内全员可见 | |
| L3 | 敏感 | 仅部门内 level>=L3 的用户可见 | |

### 3.2 用户级别

用户级别从 SSO 的 `roles` 映射：

| 用户角色 | 用户级别 | 可看到的文档密级 |
|---------|---------|----------------|
| 包含 admin | L3 | L1 + L2 + L3（全部可见） |
| 其他 | L2 | L1 + L2 |

**判断逻辑**（SSO 中间件中执行）：

```python
def _resolve_user_level(roles: list[str]) -> str:
    return "L3" if "admin" in roles else "L2"
```

**密级比较规则**：`doc_level` 从 L1→L3 权限递增。

```
L1（公开） < L2（内部） < L3（敏感）

用户 level >= 文档 doc_level  →  可见
L3 用户 → 看 L1、L2、L3
L2 用户 → 看 L1、L2
L1 用户（预留） → 仅看 L1
```

### 3.3 为什么不用角色矩阵

| 维度 | 密级（L1/L2/L3） | 角色（allowed_roles） |
|------|-----------------|---------------------|
| 理解成本 | 全员共识，"L3 最高密级" | 角色矩阵复杂，每个人不清楚哪些角色配哪些文档 |
| 新文档定权限 | 标一个数字 | 要想"哪些角色该看？" |
| 新员工入职 | 入职定级别 | 要分配 N 个角色 |
| 员工转岗 | 级别可能不变 | 角色要重新分配 |
| 计算公式 | `user.level >= doc.level` | 集合运算，组合爆炸 |

## 4. 索引流程

### 4.1 接口变更

`POST /rag/index`

**新增参数**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| department | str | "" | 部门 ID。空=公共集合，有值=对应部门集合 |
| doc_level | str | "L1" | 文档密级：L1/L2/L3 |

**映射规则**：

```
department=""   → 集合名 = "super_agent_docs"
department="103" → 集合名 = "super_agent_docs_103"
```

**核心原则**：索引时的 `department` 完全由接口参数决定，不参考用户 SSO 登录态。

**正确示例**：

```
# 全公司值班表 → 公共集合
POST /rag/index?doc_dir=data/raw_docs/值班表

# 部门 103 的日常巡检手册 → 部门集合，默认 L1
POST /rag/index?doc_dir=data/raw_docs/103&department=103

# 部门 103 的密码本 → 部门集合，L3 密级
POST /rag/index?doc_dir=data/raw_docs/密码本&department=103&doc_level=L3

# 管理员替部门 201 索引
POST /rag/index?doc_dir=data/raw_docs/201&department=201
```

**禁止行为**：不传 department 时绝不从用户 SSO 推断。

### 4.2 Metadata 结构

每个 chunk 的 metadata 中新增 `doc_level` 字段：

```python
{
    "file_path": "data/raw_docs/密码本/passwords.md",
    "doc_level": "L3",           # ← 新增
    "doc_status": "active",
    "topic_tags": [...],
    # ... 其他现有字段
}
```

移除 `department` 字段（不再需要，隔离靠集合）。

### 4.3 实现要点

```python
async def rag_index(doc_dir="data/raw_docs", department="",
                    doc_level="L1", force=False, use_llm=False, tenant_id=""):
    effective_tenant = department or tenant_id
    store = get_store(tenant_id=effective_tenant)
    # 索引过程中把 doc_level 写入每个 chunk 的 metadata
```

## 5. 检索流程

### 5.1 改造后的检索策略

| 用户类型 | 检索的集合 | 检索器 |
|---------|-----------|--------|
| 无部门（未登录/匿名） | `super_agent_docs` | `Retriever`（单 store） |
| 普通用户（department=103） | `super_agent_docs_103` + `super_agent_docs` | `MultiStoreRetriever`（双 store） |
| 超管（roles 包含 admin） | 全部 `super_agent_docs_*` + `super_agent_docs` | `MultiStoreRetriever`（多 store） |

### 5.2 密级过滤

检索时自动注入密级过滤条件：

```python
def _build_filters(self, user, user_filters=None):
    result = {"doc_status": {"$eq": "active"}}

    # 密级过滤：用户 level 决定了能看的文档级别上限
    # L3 用户 → L1+L2+L3 全部可见
    # L2 用户 → 仅 L1+L2
    # L1 用户 → 仅 L1
    if user and user.doc_level:
        result["doc_level"] = {"$in": allowed_levels(user.doc_level)}

    if user_filters:
        result.update(user_filters)

    return result

def allowed_levels(user_level: str) -> list[str]:
    """用户能看的密级列表：level=N 可看所有 <=N 的级别。"""
    ORDER = {"L1": 1, "L2": 2, "L3": 3}
    max_lv = ORDER.get(user_level, 2)  # 默认 L2
    return [lv for lv, order in ORDER.items() if order <= max_lv]
```

### 5.3 MultiStoreRetriever

新增检索器，接受指定的多个 store，内部做 RRF 融合：

```python
class MultiStoreRetriever:
    def __init__(self, stores, embedder, es_client=None):
        self.stores = stores
        self.embedder = embedder
        self.es_client = es_client

    def retrieve(self, query, top_k=5, filters=None, user=None):
        query_emb = self.embedder.embed_query(query)
        all_results = []
        for store in self.stores:
            results = store.search(query_emb, top_k * 3, filters)
            all_results.append([SearchResult(chunk=c, score=1.0) for c in results])
        fused = reciprocal_rank_fusion(*all_results, k=60)
        return [r.chunk for r in deduplicate_overlaps(fused)[:top_k]]
```

## 6. 涉及修改的文件

| 文件 | 改动 |
|------|------|
| `main.py` | `rag_index` 新增 department、doc_level 参数；`_build_retriever` 改为多 store 策略 |
| `retriever.py` | 新增 `MultiStoreRetriever` 类；`_build_filters` 增加密级过滤、移除旧 department 逻辑 |
| `stores/__init__.py` | 可能需调整 discover_tenant_collections 排除公共集合 |
| `metadata.py` | 移除 `department` 自动推断；新增 `doc_level` 字段 |
| `chunkers/semantic.py` | `_make_chunk` 中去掉 `department` 相关 metadata |
| `models.py` | `UserContext` 新增 `doc_level` 字段；`MetadataSchema` 新增 `doc_level` |
| `api/sso.py` | 从 JWT 或配置中获取用户 doc_level |

## 7. 向后兼容

- `tenant_id` 参数保留，`department` 为空且 `tenant_id` 有值时走原有逻辑
- 已索引到 `super_agent_docs` 且带 `department` metadata 的旧数据：建议重新索引
- 旧数据没有 `doc_level` 字段：`_build_filters` 中 doc_level 为空时不限制，兼容

## 8. 后续扩展

- doc_level 后期可扩展为更细粒度（L4、L5），但对上层代码影响小（只改级别映射表）
- 如果届时需要以角色控制，可以在 metadata 中恢复 `allowed_roles` 字段，过滤器改为"密级 OR 角色"逻辑
