# Phase 1 企业级 RAG 缺陷与改进分析

> 基于 2026-07-14 代码审查，逐条记录 Phase 1 RAG 模块的缺陷与改进点，待逐条讨论确认后形成正式设计文档。

---

## 一、检索质量缺陷（核心问题）

### 1. Reranker 被禁用且无替代
- ~~`BGEReranker` 直接 raise RuntimeError，被禁用但无远程 Reranker API 替代~~
- ~~当前检索链路：query → embed → 向量搜索，没有重排序步骤~~
- ~~Top-1/Top-3 准确率无法保证~~
- **→ 已确认忽略：不使用本地 embedding，不需要本地 reranker**

---

### 2. BM25 混合检索实际未生效
- **→ 已修复（完整实现：双存储架构，Elasticsearch BM25 + 向量双路检索 → RRF 融合）**
  - 新增 `ESClient`（`knowledge/es_client.py`）：ik 中文分词、索引管理、bulk 写入、BM25 检索、按文件路径删除
  - Config 新增 `ESConfig` + `RAGConfig.enable_bm25_hybrid` 开关
  - Indexer 构建时同步写入 ES；删除文件时同步清理 ES
  - Retriever 双路检索（向量 + ES BM25）→ RRF 融合 → 去重 → Top-K
  - Docker Compose 新增 ES 服务（8.12.2，单节点，512M 堆内存）
  - 依赖：`elasticsearch>=8.12` 已加入 pyproject.toml
  - `.env.dev` 默认 `SA_RAG_ENABLE_BM25_HYBRID=true`，`.env.prod` 默认 `false`

### 3. Query Expansion / Intent Classification 默认关闭
- 文件：`src/super_agent/config.py:141-143`
- `enable_query_expansion=False`、`enable_intent_classification=False`
- Intent 分类实现仅为关键词匹配，非 LLM 判别
- **→ 已确认处理：Query Expansion 保持关闭（收益不确定，代价确定）；Intent Classification 打开（代价接近零）；配置项已添加注释说明**
- **→ Intent 分类当前为关键词匹配，足够用，不升级为 LLM**

### 4. LLMAssistedChunker 分割后缺乏递归兜底
- 文件：`src/super_agent/knowledge/chunkers/llm_assisted.py:148-163`
- `_apply_split_points` 对超出 max_chunk_size 的段直接保留完整文本，不递归分割
- 大段落可能产生超长 chunk
- **→ 已修复：超过 max_chunk_size 的段 fallback 到 SemanticChunker._split_large_section 做句子级二次切割**

---

## 二、文档生命周期管理缺陷

### 5. 增量索引不处理已删除文件
- 文件：`src/super_agent/knowledge/indexer.py:32-80`
- `build()` 遍历目录新增/变更文件，但不清理"已从目录删除但向量库中仍有旧 chunks"的幽灵数据
- **→ 已修复：state 现记录每个文件的 chunk_ids，build 完成后检测过期文件并自动清理向量库 chunks**

### 6. metadata 时间戳固定为当前时间
- 文件：`src/super_agent/knowledge/metadata.py:63-64`
- `created_at` / `updated_at` 始终 `datetime.now()`，未读取文件系统实际时间
- 无法追溯文档真实版本历史
- **→ 已确认忽略：文件系统 mtime 意义不大，MD5 + doc_version 已覆盖版本追踪需求**

### 7. PDF 跨页表格合并未实现
- 文件：`src/super_agent/knowledge/loaders/pdf.py`
- spec 5.2 要求"PDF 跨页表格自动合并"，当前仅逐页提取文本
- 无跨页内容合并逻辑
- **→ 已修复：使用 PyMuPDF `page.find_tables()` 检测页底跨页表格，自动合并连续页内容为一个 Document**

---

## 三、测试覆盖缺口

### 8. 缺失以下模块的单元测试
- `QueryProcessor`（query_rewrite / expansion / intent_classification）
- `AnswerGenerator`（解析、引用生成、fallback 逻辑）
- `AuditLogger`
- `FanOutRetriever`
- `LLMAssistedChunker`
- PDF OCR 扫描页处理
- **→ 已修复：新增 `tests/unit/test_missing_coverage.py`，24 个测试全部通过**

### 9. 集成 / e2e 测试过于简单
- 需要覆盖完整链路：摄入 → 索引 → 检索 → 生成 → 审计落库
- **→ 后续补充**

---

## 四、运维与生产化缺陷

### 10. LLM 调用缺乏连接池与重试
- `query_processor.py` 和 `generator.py` 直接 `httpx.post()`
- 无连接池复用、无指数退避重试、无熔断
- OneAPI 抖动会导致检索链路断裂
- **→ 已修复：新增 `LLMClient`（`knowledge/llm_client.py`），统一封装所有 LLM chat 调用**
  - 连接池复用（httpx.Client keep-alive）
  - 指数退避重试（最多 3 次，1s → 2s → 4s）
  - 可重试状态码：429 / 500 / 502 / 503 / 504
  - 超时/连接错误自动重试
  - 已替换 `query_processor.py` / `generator.py` / `llm_assisted.py` 中的原始 `httpx.post()`

### 11. 缺乏 API 认证与速率限制
- `/rag/query`、`/rag/index` 等端点无认证、无速率限制
- 直接暴露在生产环境有安全风险
- **→ 已实现：OAuth2 SSO 认证（Authorization Code 模式）**
  - 新增 `api/sso.py`：SSO 认证模块
  - **流程**（匹配 sso.md 文档）:
    1. `GET /auth/login` → 302 重定向到授权中心 authorize URL
    2. 用户登录后 → 授权中心回调 `GET /auth/callback?code=xxx`
    3. 后端换 token → 调 `/system/user/getInfo` 拿用户信息
    4. 创建 HMAC-SHA256 签名会话 → 写 httpOnly cookie → 302 到前端
    5. 后续请求 cookie（或 `Authorization: Bearer`） → `SSOMiddleware` 校验
  - **中间件**: `SSOMiddleware` 注入 `request.state.user`（UserContext）
  - **端点**: `/auth/login`、`/auth/callback`、`/auth/logout`、`/auth/me`
  - **配置**: `SA_SSO_ENABLE`（dev=false, prod=true）

### 12. 追踪仅 Prometheus 埋点，未接 LangSmith/OTel
- `tracing/metrics.py` 只有基础 Prometheus 指标
- 未实现 spec F7 规划的 LangSmith 开发期追踪 + OTel 生产期全链路追踪
- **→ 已修复：实现 LangSmith + OpenTelemetry 双轨追踪**
  - 新增 `tracing/setup.py`：统一初始化入口
    - `_setup_langsmith()` — 配置环境变量 LANGCHAIN_TRACING_V2=true
    - `_setup_otel()` — 初始化 OTel SDK，OTLP exporter → Jaeger（端口 4317）
    - 导出全局 `tracer` 对象供应用内各模块使用
  - `main.py` lifespan 中调用 `setup_tracing()`
  - `main.py` RAG 流程添加 OTel spans：`query_processor` / `retrieval` / `answer_generation`
  - 各核心模块添加 `@traceable` 装饰器：
    - `QueryProcessor.process`、`_rewrite`、`_expand`（`run_type="llm"`）
    - `Retriever.retrieve`（`run_type="chain"`）
    - `AnswerGenerator.generate`（`run_type="chain"`）
    - `Indexer.build`（`run_type="chain"`）
    - `LLMClient.chat`（`run_type="llm"`）
  - Config：`TracingConfig` 已预置（langsmith_api_key / enable_langsmith / enable_otel / otel_exporter / otel_service_name）
  - Docker Compose：Jaeger all-in-one 已在配置中（端口 16686 UI / 4317 gRPC）
  - 依赖：opentelemetry-api / opentelemetry-sdk / opentelemetry-exporter-otlp / langsmith 已在 pyproject.toml 中

### 13. 提示词硬编码
- `_REWRITE_PROMPT` / `_EXPANSION_PROMPT` / `_DEFAULT_SYSTEM_PROMPT` / `_BOUNDARY_PROMPT`
- 全部硬编码在 Python 文件中，未实现模板化管理和热加载
- **→ 已修复：新建 `prompts/` 模块（Jinja2 模板引擎）**
  - `prompts/__init__.py`：`get_prompt(name, **kwargs)` / `register_prompt(name, content)` / `list_prompts()`
  - `prompts/templates/*.jinja2`：4 个模板文件（query_rewrite / query_expansion / qa_system / boundary_split）
  - 每个模板文件均带有完整的中文注释说明用途和变量
  - 已替换 `query_processor.py` / `generator.py` / `llm_assisted.py` 中的全部硬编码提示词

---

## 五、数据模型与接口缺陷

### 14. SearchResult metadata 类型不安全
- `chunk.metadata` 是普通 dict，字段类型未校验
- 多 Store 实现间 metadata 格式可能存在不一致风险

### 15. 缺乏批量检索与流式输出
- 仅支持单 Query 检索 + 完整生成
- 不支持批量 query、SSE 流式输出答案
- 大文档场景用户等待时间长
- **→ 已实现：批量检索 + SSE 流式输出**
  - `POST /rag/query/stream` — SSE 流式端点，LLM 逐 token 输出
    - SSE 事件格式：`sources`（检索源文档）→ `token`（逐字输出）→ `citations`（引用列表）→ `done`
    - 前端可用 EventSource 或 fetch + ReadableStream 消费，用户无需等待完整生成
  - `POST /rag/batch-query` — 批量查询端点
    - 接受 `{queries: [{query, top_k, filters, ...}]}`，内部 `asyncio.gather` 并行执行
    - 返回 `{results: [QueryResponse]}`，顺序与输入一致
  - `LLMClient.chat_stream()` — 流式 LLM 调用（`stream: true`，SSE line-by-line 解析）
  - `AnswerGenerator.generate_stream()` — 流式答案生成器，按 SSE 事件格式产出
