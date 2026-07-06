# Super Agent

企业级 AI 应用开发平台 — 知识库模块（Phase 1）

提供本地高质量知识库的完整管线：文档摄入 → 语义切分 → 向量化 → 检索，支持 Docker Compose 一键部署。

## 功能概览

| 能力 | 说明 |
|------|------|
| 多格式文档加载 | PDF / Word / Markdown / HTML / JSON / YAML / CSV |
| 语义结构切分 | 标题继承分组、可配置 overlap 比率、页码保留 |
| 可插拔 Embedding | 本地 BGE-large-zh-v1.5 / 远程 Embedding API |
| 可插拔向量库 | ChromaDB（轻量） / Milvus（生产级） |
| 混合检索 | 向量搜索 + BM25 关键词搜索 + RRF 融合 |
| Rerank 重排 | BGE-reranker-v2-m3 精排 |
| Metadata 多标签过滤 | topic_tags 支持 `$contains` / `array_contains` 过滤 |
| 增量 / 全量索引 | 文件哈希追踪，仅处理新增或变更文件 |
| REST API | `/rag/query` 检索、`/rag/index` 索引、`/health` 健康检查 |

## 技术栈

Python 3.12+ · uv · LangChain · LangGraph · LangServe · ChromaDB / Milvus · BGE · Redis · MySQL · Docker Compose · Pydantic Settings · pytest

## 项目结构

```
super_agent/
├── pyproject.toml
├── docker-compose.yml
├── Dockerfile
├── .env.dev
├── .env.prod
│
├── src/super_agent/
│   ├── main.py                    # FastAPI 入口
│   ├── config.py                  # Pydantic Settings 统一配置
│   └── knowledge/
│       ├── models.py              # Chunk / SearchResult / MetadataSchema
│       ├── metadata.py            # topic_tags 解析（手动 > 目录 > LLM）
│       ├── loaders/               # 6 种文档加载器 + 注册表
│       ├── chunkers/              # SemanticChunker
│       ├── embedders/             # BGEEmbedder / APIEmbedder
│       ├── stores/                # ChromaStore / MilvusStore
│       ├── bm25.py                # BM25 关键词搜索
│       ├── reranker.py            # BGE Reranker
│       ├── retriever.py           # 检索编排（混合 + RRF + 去重）
│       └── indexer.py             # 增量 / 全量索引管线
│
├── tests/
│   ├── unit/                      # 单元测试
│   ├── integration/               # 集成测试
│   └── e2e/                       # 端到端测试
│
├── data/
│   ├── raw_docs/                  # 原始文档目录
│   └── index_state/               # 索引状态追踪
│
└── deploy/docker/
    ├── mysql/init.sql
    └── otel/
```

## 本地开发

### 1. 环境准备

```bash
# 安装 uv（若未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 克隆项目
git clone <repo-url> && cd super_agent
```

### 2. 安装依赖

```bash
# 基础依赖
uv sync

# 如需本地 BGE 模型（约 1.3GB），安装 ML 可选组
uv sync --extra ml
```

### 3. 配置环境变量

```bash
# 复制开发环境配置并按需修改
cp .env.dev .env
```

关键配置项说明见 `.env.dev` 文件内注释。最简配置只需修改：

```bash
SA_LLM_ONEAPI_API_KEY=sk-your-actual-key   # OneAPI 密钥
```

### 4. 启动基础设施（可选）

如需 Redis 和 MySQL，可通过 Docker 启动：

```bash
docker compose up -d redis mysql
```

### 5. 启动服务

```bash
# 开发模式（自动重载）
uv run uvicorn super_agent.main:app --reload --port 8000

# 或通过入口脚本
uv run super-agent
```

### 6. 构建索引 & 查询

```bash
# 将文档放入 data/raw_docs/ 目录，然后触发索引构建
curl -X POST http://localhost:8000/rag/index

# 检索查询
curl -X POST http://localhost:8000/rag/query \
  -H "Content-Type: application/json" \
  -d '{"query": "MySQL主从延迟怎么排查", "top_k": 5}'
```

### 7. 运行测试

```bash
# 全量测试
uv run pytest

# 仅单元测试
uv run pytest tests/unit/ -v

# 代码检查
uv run ruff check src/ tests/
```

## 生产部署

### 1. 准备环境变量

```bash
# 复制生产配置模板
cp .env.prod .env
```

修改 `.env` 中的敏感变量（通过宿主机环境变量注入）：

```bash
export ONEAPI_API_KEY="sk-prod-key"
export REDIS_PASSWORD="strong-redis-pw"
export MYSQL_PASSWORD="strong-mysql-pw"
export EMBEDDING_API_URL="https://your-embedding-service/v1"
export EMBEDDING_API_KEY="sk-embedding-key"
```

同时修改以下生产必需项：

```bash
SA_SERVER_CORS_ORIGINS='["https://your-domain.com"]'   # 生产禁止通配符
SA_SERVER_LOG_LEVEL=INFO                                # 生产建议 INFO
```

### 2. 一键部署

```bash
docker compose up -d
```

这将启动以下服务：

| 服务 | 端口 | 说明 |
|------|------|------|
| app | 8000 | 主应用 |
| chroma | 8001 | 向量库（开发默认） |
| redis | 6379 | 缓存 & 短时记忆 |
| mysql | 3306 | 持久化存储 |
| oneapi | 3000 | LLM 统一网关 |
| jaeger | 16686 | 链路追踪 UI |

### 3. 验证部署

```bash
# 健康检查
curl http://localhost:8000/health

# 构建索引
curl -X POST http://localhost:8000/rag/index?doc_dir=data/raw_docs

# 查询测试
curl -X POST http://localhost:8000/rag/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Redis缓存击穿如何处理", "top_k": 3}'
```

### 4. 生产切换 Milvus

如需更高性能的向量库，修改 `.env`：

```bash
SA_VECTOR_PROVIDER=milvus
SA_VECTOR_MILVUS_HOST=milvus       # docker compose 服务名
SA_VECTOR_MILVUS_PORT=19530
```

并在 `docker-compose.yml` 中添加 Milvus 服务：

```yaml
  milvus:
    image: milvusdb/milvus:v2.4-latest
    ports:
      - "19530:19530"
      - "9091:9091"
    volumes:
      - milvus-data:/var/lib/milvus
    restart: unless-stopped
```

### 5. 查看链路追踪

访问 Jaeger UI：`http://localhost:16686`

## API 参考

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/rag/query` | 知识库检索，body: `{query, top_k?, filters?}` |
| POST | `/rag/index` | 触发索引构建，param: `doc_dir` |

## License

Internal Use Only
