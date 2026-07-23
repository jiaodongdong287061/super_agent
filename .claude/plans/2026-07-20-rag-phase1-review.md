# Enterprise RAG Phase 1 现状审视与改进方向

> 基于代码实际状态 vs 规格文档需求的全面审视（2026-07-20）

---

## 一、已完成能力

| 模块 | 状态 | 说明 |
|------|------|------|
| 9 种文档加载器 | ✅ | PDF/Word/MD/HTML/TXT/JSON/CSV/Excel/PPT，工厂注册模式 |
| 语义切分器 | ✅ | 标题链继承、句子级 overlap、Embedding 语义边界检测、跨页合并 |
| 向量存储 | ✅ | Chroma/Milvus/Qdrant 三套实现，可插拔 |
| 多集合隔离 | ✅ | 部门级物理隔离 + MultiStoreRetriever + RRF 融合 |
| 文档密级 | ✅ | L1/L2/L3，SSO 角色映射，检索时自动过滤 |
| 混合检索 | ✅ | 向量 + ES BM25 + Local BM25 → RRF → 去重 |
| Query 理解 | ✅ | 改写 + 扩展 + 意图分类（LLM 驱动，可配置开关） |
| 答案生成 | ✅ | 含引用解析、流式 SSE 输出 |
| 审计日志 | ✅ | 异步 MySQL 写入，非阻塞 |
| SSO 集成 | ✅ | OAuth2 + JWT，自动注入用户上下文 |
| 增量索引 | ✅ | MD5 哈希追踪，每文件粒度 |
| 可观测性 | ✅ | LangSmith + Prometheus metrics |

---

## 二、需要完善的地方

### 1. Reranker 完全不可用（P0 - 高优先级）

**位置**：`src/super_agent/knowledge/reranker.py`

**问题**：`BGEReranker.__init__` 直接 `raise RuntimeError`，本地 FlagEmbedding 被移除后没有替换方案。规格文档明确要求 BGE-reranker-v2-m3 重排序，当前检索流程跳到 Rerank 步骤就直接崩溃。

**建议**：用远程 Reranker API 替代（如 BGE 或 Cohere 的在线 reranker），或重新引入可选的本地 FlagEmbedding。

**影响**：🔴 检索缺少精排，Top-1 准确率受限

---

#### 补充：什么是 Reranker

Reranker（重排序器）是检索流程中**在向量召回之后、交给 LLM 之前**的一个精排环节。

**为什么需要 Reranker**

向量检索只能找到"语义上相似"的文档，但"语义相似 ≠ 真正有用"。比如搜"MySQL 主从延迟怎么排查"，向量检索会给出一堆关于 MySQL 主从的文档，但可能排在最前面的几篇讲的是原理而不是排查步骤。

**完整检索流程**

```
用户 query "MySQL主从延迟怎么排查"
        │
        ▼
  QueryProcessor
  ├─ Rewrite（LLM 改写） → "MySQL 主从延迟排查方法"
  ├─ Intent Classification → "qa" | "summarize" | "instruction"
  └─ Expansion（LLM 扩展，默认关闭）→ ["主从同步延迟排查", ...]
        │
        ▼
  Embedding → 向量检索（cosine相似度）召回 top_k * 3
        │
        ▼
  ES BM25（可选，关键词检索）召回 top_k * 3
        │
        ▼
  RRF 融合（多路召回按排名合并）
        │
        ▼
  去重（overlap 子块合并，保留最高分）
        │
        ▼
  Reranker（交叉编码器精排取 top_k） ← 默认关闭
        │
        ▼
  LLM 生成答案（带来源引用 [N]）
```
        │
        ▼
  LLM 生成答案
```

**关键区别**

| 维度 | 向量检索 | Reranker |
|------|---------|----------|
| 模型类型 | 双编码器（Bi-Encoder） | 交叉编码器（Cross-Encoder） |
| 输入 | query 单独编码，文档单独编码 | query + 文档拼成一对一起编码 |
| 速度 | 快（百万级/秒） | 慢（几百对/秒） |
| 精度 | 一般（只看到语义方向） | 高（能看到 query 和文档的精确匹配关系） |
| 适用阶段 | 第一轮粗筛，召回候选集 | 第二轮精排，对少量候选重排序 |

**打个比方**：向量检索像海选 — 从 10000 人里快速筛出 30 个看起来差不多的候选人。Reranker 像终面 — 对这 30 个人逐一深度面谈，挑出最匹配的 5 个。这就是为什么规格文档要求 BGE-reranker-v2-m3：Top-1 的准确率主要靠 Reranker 提上来的，光靠向量检索很难做到。

---

### 2. LLM 自动打标未实现（P1 - 中优先级）

**位置**：`src/super_agent/knowledge/metadata.py`

**问题**：规格文档 5.3.1 节明确列出"LLM 自动打标"作为 `topic_tags` 的第三来源（优先级：手动标注 > 目录继承 > LLM 自动打标），但代码中只有手动标注和目录继承两种方式。

**建议**：在 `build_metadata` 中增加 LLM 兜底分支，当 `topic_tags` 为空时调用 LLM 分析内容生成标签。

**影响**：🔶 大量无目录结构的散落文档缺少 topic_tags，检索时无法通过标签过滤。

---

### 3. 无反馈闭环（P1 - 中优先级）

**问题**：检索结果和生成答案没有任何用户反馈机制（点赞/点踩/纠正），无法持续改进检索质量。

**建议**：
- 增加 `POST /rag/feedback` 端点，记录 chunk_id 的有用/无用标记
- 数据可后续用于微调 Embedding 或优化排序

**影响**：🔶 无法持续优化检索质量，问题反复出现

---

### 4. 无评估管线（P1 - 中优先级）

**问题**：没有任何自动化评估工具来衡量检索和生成质量：
- 召回率（Recall@K）
- 排序质量（MRR、NDCG）
- 答案质量（faithfulness、relevance）

**建议**：在 `tests/` 下新增 `eval/` 目录，用真实 query + 标注数据定期跑评测，产出量化报告。

**影响**：🔶 无法量化检索效果，迭代优化缺乏依据

---

### 5. 文档管理能力缺失（P2 - 中优先级）

**问题**：
- 有 `/rag/doc/list` 和 `/rag/doc/status`，但**没有文档删除/版本回滚** API
- 索引状态文件在 `data/index_state/` 下按租户分开，但**没有暴露版本历史**，只能看到当前版本号
- **没有文件监控**：新增文档需要手动调 `/rag/index`，不能自动发现

**建议**：
- 增加文件监视器（`watchdog`）自动触发增量索引
- 提供 `/rag/doc/rollback` 接口
- 增加 `doc_version` 历史查看 API

**影响**：🔸 运维不便，大量手动操作

---

### 6. 本地 BM25 内存级问题（P2 - 中优先级）

**位置**：`src/super_agent/knowledge/bm25.py`

**问题**：`BM25Search` 基于内存，`index()` 后数据只存在当前进程，服务重启就丢失。`search()` 在 `_bm25` 为 None 时直接返回空列表。`Retriever.__init__` 虽有 `use_hybrid` 参数，但实际构建时没有注入 BM25 实例。

**建议**：要么废弃本地 BM25 完全走 ES，要么将其持久化（如 sqlite 存储词频）。

**影响**：🔸 混合检索降级，Local BM25 名存实亡

---

### 7. MultiStoreRetriever 缺少 BM25 支持（P2 - 低优先级）

**位置**：`src/super_agent/knowledge/retriever.py`

**问题**：`MultiStoreRetriever` 没有接入 BM25，只有 `Retriever` 支持。部门隔离场景的用户走 `MultiStoreRetriever`，意味着混合检索对多 store 用户无效。

**影响**：🔸 部门隔离用户无混合检索

---

### 8. 配置项收敛（P3 - 低优先级）

**问题**：
- `enable_bm25_hybrid` 默认 `False`，需手动开启
- `enable_query_expansion` 默认 `False` — 合理但文档未说明原因
- ES 相关配置分散在 `ESConfig` 和 `RAGConfig`
- 部分配置缺少 enum 校验或取值范围注释

**建议**：整理配置收敛，增加注释说明默认值选择原因。

---

### 9. 无速率限制和请求缓存（P3 - 低优先级）

**问题**：
- 生产环境下 `/rag/query` 没有限流，可能被滥用
- 相同 query 重复请求每次都重新 embedding + 搜索，没有结果缓存

**建议**：增加 LRU 缓存（或 Redis 缓存），TTL 可配置；API 层加限流中间件。

---

### 10. 无 MCP 接口（按计划 — Phase 3）

**位置**：规划于 Phase 3

**问题**：规格文档 7.1 节规划了 `knowledge_search` / `knowledge_index` 等 MCP Server 能力，当前尚未实现。

**说明**：属于 Phase 3 范围，当前阶段不视为遗漏。

---

## 三、优先级汇总

| 优先级 | 事项 | 影响 |
|--------|------|------|
| **P0** | Reranker 不可用 | 🔴 检索缺少精排，Top-1 准确率受限 |
| **P1** | LLM 自动打标缺失 | 🔶 大量文档缺少 topic_tags |
| **P1** | 无反馈闭环 | 🔶 无法持续优化检索质量 |
| **P1** | 无评估管线 | 🔶 无法量化检索效果 |
| **P2** | 文档管理能力（删除/版本/监控） | 🔸 运维不便 |
| **P2** | 本地 BM25 不可用 | 🔸 混合检索降级 |
| **P2** | MultiStore 缺 BM25 | 🔸 部门隔离用户无混合检索 |
| **P3** | 速率限制 / 缓存 | 生产化必备 |
| **P3** | 配置项收敛 | 可维护性 |
| Phase 3 | MCP 接口 | 规划内不视为遗漏 |
