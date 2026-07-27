# 企业级 RAG 缺失能力补齐 — 实施计划

## Context

当前 RAG 系统已完成：多格式加载、向量化、语义检索、混合检索+Reranker、标签体系、增量索引、向量库可切换。根据 `docs/superpowers/specs/企业级 RAG 知识库缺失能力.md`，需要补齐 8 项企业级缺失能力。用户要求一次性全部实现。

**核心原则**：
- 不改变现有工厂注册模式（loader/chunker/embedder/store）
- 复用在 config.py 中已有的 LLMConfig（OneAPI 统一网关）
- 多租户 = 多部门，使用 `department` metadata 字段做逻辑过滤，不做物理隔离

## 实施顺序（4 个 Phase）

```
Phase A: 核心 LLM 集成（基础）
  1. LLM 答案生成          ← 最关键
  2. 可观测性              ← 可并行

Phase B: 查询管线增强
  3. 查询理解（LLM 改写）
  4. 语义分块增强（LLM 辅助）

Phase C: 治理与安全
  5. 权限控制（metadata 扩展 + 过滤器注入）
  6. 多租户（department 自动过滤）
  7. 审计追踪（MySQL + 异步写入）

Phase D: 文档生命周期
  8. 文档管理（版本、过期、状态）
```

---

## Phase A: 核心 LLM 集成

### 1. LLM 答案生成

**新建文件**：
- `src/super_agent/knowledge/generator.py` — `AnswerGenerator` 类
- `src/super_agent/knowledge/prompts/` — 提示词模板目录

**修改文件**：
- `src/super_agent/knowledge/models.py` — 添加 `Citation`, `GeneratedAnswer` 数据类
- `src/super_agent/main.py` — 修改 `/rag/query`：检索后调用 LLM 生成带引用的答案；在 QueryRequest 中增加 `system_prompt`, `temperature` 字段；在 QueryResponse 中增加 `citations` 字段

**核心逻辑**：
- 将 chunks 编号为 `[1]`, `[2]`... 格式的上下文
- 调用 OneAPI `/chat/completions`（复用 httpx，与 embedders/api.py 一致）
- 解析 LLM 回答中的 `[N]` 引用标记
- 解析失败降级为当前行为（直接拼接 chunks）

### 2. 可观测性

**新建文件**：
- `src/super_agent/tracing/metrics.py` — Prometheus 指标定义

**修改文件**：
- `src/super_agent/main.py` — 添加 `GET /metrics` 端点，对 `/rag/query` 做耗时埋点
- `src/super_agent/config.py` — 添加 `RAGConfig` 配置类（SA_RAG_ 前缀）
- `pyproject.toml` — 添加 `prometheus-client>=0.19` 依赖

**指标清单**：
| 指标名 | 类型 | 标签 |
|--------|------|------|
| `rag_queries_total` | Counter | status |
| `rag_retrieval_duration_seconds` | Histogram | — |
| `rag_generation_duration_seconds` | Histogram | — |
| `rag_retrieved_chunks` | Histogram | — |

---

## Phase B: 查询管线增强

### 3. 查询理解

**新建文件**：
- `src/super_agent/knowledge/query_processor.py` — `QueryProcessor` 类

**修改文件**：
- `src/super_agent/knowledge/models.py` — 添加 `ProcessedQuery` 数据类
- `src/super_agent/main.py` — 在 retriever 调用前插入 `QueryProcessor.process()`
- `src/super_agent/config.py` — 在 `RAGConfig` 中添加 `enable_query_rewrite`, `enable_query_expansion`, `enable_intent_classification` 配置

**核心逻辑**：
- query 改写（默认开启）：LLM 修正拼写/缩写，优化检索效果
- query 扩展（默认关闭）：生成 2-3 个替代表述，多路检索 + RRF 融合

### 4. 语义分块增强

**新建文件**：
- `src/super_agent/knowledge/chunkers/llm_assisted.py` — `LLMAssistedChunker` 类

**修改文件**：
- `src/super_agent/knowledge/chunkers/__init__.py` — 导出新 chunker
- `src/super_agent/knowledge/chunkers/semantic.py` — 提取可复用的 `_split_sentences`, `_estimate_tokens` 为模块级函数
- `src/super_agent/config.py` — 添加 chunker 配置（`provider`, `use_llm`）

**核心逻辑**：
- 先用规则切分（heading + sentence），对超大段落调用 LLM 建议语义断点
- LLM 失败时回退到句子级切分
- 默认关闭（`use_llm=False`），向后兼容

---

## Phase C: 治理与安全

### 5. 权限控制

**修改文件**：
- `src/super_agent/knowledge/metadata.py` — 在 `build_metadata()` 中添加 `allowed_roles`, `allowed_users`, `permission_scope` 字段
- `src/super_agent/knowledge/models.py` — 更新 `MetadataSchema`
- `src/super_agent/main.py` — 在 `QueryRequest` 中添加 `UserContext`（user_id, roles, department）
- `src/super_agent/knowledge/retriever.py` — 添加 `_build_permission_filters()`，自动注入权限过滤条件

**过滤逻辑**：
```
用户请求 → 权限过滤器组合 → store.search(filters=合并后的条件)
                                               ↑
                                    permission_filters AND user_filters
```

### 6. 多租户（多部门）

**修改文件**：
- `src/super_agent/knowledge/retriever.py` — 自动注入 `department` 过滤（复用权限过滤器机制）
- `src/super_agent/knowledge/indexer.py` — 无需修改（`build_metadata()` 已自动从目录路径继承 department）

**核心逻辑**：用户带 `department` 查询时，自动注入 `{"department": {"$eq": user.department}}`，无需物理隔离。

### 7. 审计追踪

**新建文件**：
- `src/super_agent/knowledge/audit.py` — `AuditLogger` 类（SQLAlchemy async + 异步写入）

**修改文件**：
- `deploy/docker/mysql/init.sql` — 添加 `audit_log` 表
- `src/super_agent/main.py` — 在 `/rag/query` 完成后 fire-and-forget 写入审计日志
- `src/super_agent/config.py` — 添加 `enable_audit` 配置

**核心逻辑**：
- 使用 `asyncio.create_task()` 实现 fire-and-forget
- 审计写入失败不影响查询响应

---

## Phase D: 文档生命周期

### 8. 文档管理

**修改文件**：
- `src/super_agent/knowledge/metadata.py` — 添加 `expiry_date`, `doc_status` 字段
- `src/super_agent/knowledge/models.py` — 更新 `MetadataSchema`
- `src/super_agent/knowledge/indexer.py` — 版本自增逻辑（hash 变化时 +1），过期文档跳过索引
- `src/super_agent/knowledge/retriever.py` — 自动注入 `doc_status="active"` 过滤
- `src/super_agent/main.py` — 添加文档管理 API 端点

**新增端点**：
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/rag/doc/status` | 查询文档状态 |
| POST | `/rag/doc/expire` | 标记文档过期 |
| POST | `/rag/doc/versions` | 版本历史 |

---

## 文件变更汇总

| 操作 | 文件 | 涉及能力 |
|------|------|---------|
| CREATE | `src/super_agent/knowledge/generator.py` | #1 |
| CREATE | `src/super_agent/knowledge/query_processor.py` | #3 |
| CREATE | `src/super_agent/knowledge/audit.py` | #7 |
| CREATE | `src/super_agent/knowledge/chunkers/llm_assisted.py` | #4 |
| CREATE | `src/super_agent/tracing/metrics.py` | #2 |
| CREATE | `src/super_agent/knowledge/prompts/`（目录+模板） | #1 |
| MODIFY | `src/super_agent/main.py` | #1, #2, #3, #5, #6, #7, #8 |
| MODIFY | `src/super_agent/config.py` | #1, #2, #3, #4, #7, #8 |
| MODIFY | `src/super_agent/knowledge/models.py` | #1, #3, #5, #8 |
| MODIFY | `src/super_agent/knowledge/metadata.py` | #5, #8 |
| MODIFY | `src/super_agent/knowledge/retriever.py` | #5, #6, #8 |
| MODIFY | `src/super_agent/knowledge/indexer.py` | #8 |
| MODIFY | `src/super_agent/knowledge/chunkers/__init__.py` | #4 |
| MODIFY | `src/super_agent/knowledge/chunkers/semantic.py` | #4 |
| MODIFY | `deploy/docker/mysql/init.sql` | #7 |
| MODIFY | `pyproject.toml` | #2 |
| CREATE | `tests/unit/test_generator.py` | #1 |
| CREATE | `tests/unit/test_query_processor.py` | #3 |
| CREATE | `tests/unit/test_audit.py` | #7 |
| CREATE | `tests/unit/test_metrics.py` | #2 |
| CREATE | `tests/unit/test_llm_chunker.py` | #4 |
| MODIFY | `tests/unit/test_retriever.py` | #5, #6, #8 |

## 验证方式

1. **单元测试**：`uv run pytest tests/unit/ -v`
2. **集成测试**：`uv run pytest tests/integration/ -v`
3. **手动测试**：启动服务后，对 `/rag/query` 发送带不同 user context 的请求，验证：
   - LLM 返回带 `[N]` 引用的答案
   - 不同部门用户只能看到本部门文档
   - 审计日志写入 MySQL
   - `/metrics` 端点返回 Prometheus 格式数据
