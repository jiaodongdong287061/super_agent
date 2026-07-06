# Super Agent

企业级 AI 应用开发平台，以 LangChain 生态为核心，提供从知识库构建、Agent 编排到工具集成的全栈能力。

## 项目阶段

- **Phase 1（当前）**：本地高质量知识库（RAG）— 文档摄入 → 语义切分 → 向量化 → 检索
- **Phase 2**：Agent 编排（Router/PlanExecute/Supervisor）+ 记忆系统（Redis + MySQL）
- **Phase 3**：工具生态（MCP + AgentSkill）+ Docker 沙箱 + OpenTelemetry + 工作流编排

详见 `docs/superpowers/specs/2026-06-16-super-agent-requirements.md`

## 技术栈

| 维度 | 选型 |
|------|------|
| 语言 | Python 3.12+ |
| 包管理 | uv |
| Web 框架 | FastAPI + Uvicorn |
| LLM 框架 | LangChain + LangGraph + LangServe |
| LLM 接入 | OneAPI（统一代理网关） |
| 向量库 | Chroma / Milvus / Qdrant（可插拔） |
| Embedding | BGE 本地 / 远程 API（可插拔） |
| 文档解析 | PyMuPDF / python-docx / openpyxl / BeautifulSoup / python-pptx |
| OCR | PaddleOCR（可选 `uv sync --extra ml`） |
| 缓存 | Redis |
| 持久化 | MySQL（asyncmy） |
| 追踪 | LangSmith / OpenTelemetry + Jaeger |
| 沙箱 | Docker |
| 部署 | Docker Compose |
| 测试 | pytest + pytest-asyncio |

## 目录结构

```
super_agent/
├── pyproject.toml                  # uv 项目配置 & 依赖
├── docker-compose.yml              # 一键部署
├── Dockerfile                      # 应用镜像
├── .env.dev                        # 开发环境配置模板
├── .env.prod                       # 生产环境配置模板
│
├── src/super_agent/
│   ├── main.py                     # FastAPI 入口 + API 路由
│   ├── config.py                   # Pydantic Settings 统一配置
│   │
│   ├── knowledge/                  # 知识库模块（Phase 1 核心）
│   │   ├── models.py               # Chunk / SearchResult / MetadataSchema
│   │   ├── metadata.py             # 13 字段 metadata 构建 + topic_tags 合并
│   │   ├── tags.py                 # tags.yaml 解析 + glob 文件匹配
│   │   ├── bm25.py                 # BM25 关键词搜索（jieba 分词）
│   │   ├── reranker.py             # 🔴 BGE Reranker（当前被禁用）
│   │   ├── retriever.py            # 检索编排：向量 + BM25 混合 + RRF + 去重
│   │   ├── indexer.py              # 增量/全量索引管线（MD5 哈希追踪）
│   │   ├── loaders/                # 9 种文档加载器（工厂注册模式）
│   │   │   ├── base.py             # BaseLoader 抽象接口
│   │   │   ├── pdf.py              # PyMuPDF + PaddleOCR（扫描检测）
│   │   │   ├── word.py             # .docx python-docx / .doc LibreOffice 转换
│   │   │   ├── markdown.py         # .md / .markdown
│   │   │   ├── html.py             # .html / .htm（BeautifulSoup）
│   │   │   ├── text.py             # .txt（UTF-8）
│   │   │   ├── structured.py       # .json / .yaml / .yml / .csv
│   │   │   ├── excel.py            # .xlsx（openpyxl）/ .xls（xlrd）
│   │   │   └── ppt.py              # .pptx（python-pptx）/ .ppt（LibreOffice）
│   │   ├── chunkers/
│   │   │   ├── base.py             # BaseChunker 抽象接口
│   │   │   └── semantic.py         # SemanticChunker（标题链 + 句子级 overlap）
│   │   ├── embedders/
│   │   │   ├── base.py             # BaseEmbedder 抽象接口
│   │   │   └── api.py              # APIEmbedder（远程 API，自动分批 64 条）
│   │   └── stores/
│   │       ├── base.py             # BaseVectorStore 抽象接口
│   │       ├── chroma_store.py     # ChromaDB 实现
│   │       ├── milvus_store.py     # Milvus 实现
│   │       └── qdrant_store.py     # Qdrant 实现
│   │
│   ├── core/                       # 🔜 Phase 2: Agent 编排层
│   │   ├── orchestrator.py
│   │   ├── classifier.py
│   │   ├── router.py
│   │   ├── plan_execute.py
│   │   ├── supervisor.py
│   │   └── state.py
│   │
│   ├── memory/                     # 🔜 Phase 2: 记忆系统
│   │   ├── base.py
│   │   ├── short_term.py          # Redis
│   │   ├── long_term.py           # MySQL
│   │   └── manager.py
│   │
│   ├── tools/                      # 🔜 Phase 3: 工具生态
│   │   ├── custom/
│   │   ├── mcp_client.py
│   │   ├── mcp_server.py
│   │   └── skill_loader.py
│   │
│   ├── prompts/                    # 🔜 Phase 2: 提示词编排
│   │   ├── registry.py
│   │   ├── templates/
│   │   └── versioning.py
│   │
│   ├── sandbox/                    # 🔜 Phase 3: Docker 沙箱
│   │   ├── docker_manager.py
│   │   └── profiles.py
│   │
│   └── tracing/                    # 🔜 Phase 3: 可观测性
│       ├── langsmith.py
│       └── otel.py
│
├── tests/
│   ├── unit/                       # 单元测试
│   ├── integration/                # 集成测试
│   └── e2e/                        # 端到端测试
│
├── data/
│   ├── raw_docs/                   # 文档存放目录
│   └── index_state/                # 索引状态（MD5 哈希追踪）
│
├── deploy/docker/
│   ├── mysql/init.sql
│   └── otel/
│
└── docs/superpowers/specs/         # 设计文档
```

## 开发命令

```bash
# 安装依赖
uv sync
uv sync --extra ml          # 安装 PaddleOCR（可选，扫描 PDF OCR）

# 启动服务（开发模式，热重载）
uv run uvicorn super_agent.main:app --reload --port 8000

# 运行测试
uv run pytest                                        # 全量
uv run pytest tests/unit/ -v                         # 仅单元
uv run pytest tests/unit/test_xxx.py -v -k "test"    # 指定文件/用例

# 代码检查
uv run ruff check src/ tests/

# Docker 部署
docker compose up -d
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/rag/query` | RAG 检索 `{query, top_k?, filters?}` |
| POST | `/rag/index` | 构建索引 `?doc_dir=...&force=false` |
| POST | `/rag/delete` | 删除/清空 `{chunk_ids?}` |

## 配置

所有配置通过环境变量注入，前缀规则 `SA_` → 子配置前缀：

| 配置类 | 前缀 | 关键项 |
|--------|------|--------|
| LLMConfig | `SA_LLM_` | oneapi_base_url, oneapi_api_key, default_model |
| EmbeddingConfig | `SA_EMBEDDING_` | provider, api_url, api_key, api_batch_size |
| VectorStoreConfig | `SA_VECTOR_` | provider, qdrant_url, qdrant_vector_size, qdrant_distance |
| OCRConfig | `SA_OCR_` | enabled, text_threshold(默认0.1) |
| ServerConfig | `SA_SERVER_` | port, log_level, cors_origins |

## 关键设计约定

### 模块接口

所有核心模块通过抽象基类解耦，遵循工厂注册模式：

```
Loader:   BaseLoader → get_loader(ext) → PDFLoader/WordLoader/...
Chunker:  BaseChunker → SemanticChunker
Embedder: BaseEmbedder → get_embedder() → APIEmbedder/BGEEmbedder
Store:    BaseVectorStore → get_store() → ChromaStore/MilvusStore/QdrantStore
```

### Metadata 标签体系（13 字段）

`doc_source / doc_type / department / topic_tags / system_name / severity / created_at / updated_at / chunk_type / parent_chunk_id / page_numbers / heading_path / doc_version / file_path`

`topic_tags` 三级来源（优先级）：手动标注 > tags.yaml > 目录路径继承

### Overlap 策略

- 标题继承：每个 chunk 前置完整标题链，不计入 chunk size
- 句子级重叠：`overlap_ratio` 默认 0.15（text）/ 0（table/code）/ 0.20（list）
- 跨页合并：相邻同类型元素（表格/代码）自动合并

### 检索流程

query → embed → 向量搜索(3×top_k) → (可选 BM25 + RRF) → (可选 Rerank) → 去重 → Top-K

### 增量索引

MD5 文件哈希追踪，`data/index_state/index_state.json` 持久化状态。
`tags.yaml` 放在 `doc_dir` 根目录，支持精确文件名匹配和 glob 模式匹配。
