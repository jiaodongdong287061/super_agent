# Super Agent 系统设计文档

## 1. 架构总览

### 1.1 架构模式
模块化分层架构（方案 B），按职责拆分为独立模块，模块间通过 Python 接口通信，单进程可部署，代码边界清晰。

### 1.2 架构图

```
                         ┌─────────────┐
                         │   Client    │
                         └──────┬──────┘
                                │ HTTP/SSE
                         ┌──────▼──────┐
                         │  LangServe   │  ← REST API 层
                         │  (FastAPI)   │
                         └──────┬──────┘
                                │
                    ┌───────────▼───────────┐
                    │    Orchestrator       │  ← 编排层
                    │  (LangGraph Graph)    │
                    │                       │
                    │  Classifier → Route   │
                    │     ├─ Router Graph   │
                    │     ├─ PlanExec Graph │
                    │     └─ Supervisor Grp │
                    └───────┬───────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
   ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
   │  Knowledge  │  │   Memory    │  │   Tools     │  ← 能力层
   │   Module    │  │   Module    │  │   Module    │
   ├─────────────┤  ├─────────────┤  ├─────────────┤
   │ Loaders     │  │ ShortTerm   │  │ Custom      │
   │ Chunkers    │  │ (Redis)     │  │ MCP Server  │
   │ Embedders   │  │ LongTerm    │  │ MCP Client  │
   │ Stores      │  │ (MySQL)     │  │ SkillLoader │
   │ Retriever   │  │ Manager     │  │ Sandbox     │
   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
          │                │                 │
   ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
   │ Chroma /    │  │   Redis /   │  │   Docker    │  ← 基础设施层
   │   Milvus    │  │   MySQL     │  │             │
   └─────────────┘  └─────────────┘  └─────────────┘
                            │
                    ┌───────▼───────┐
                    │    OneAPI     │  ← LLM 代理层
                    └───────┬───────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
         ┌────▼───┐   ┌────▼───┐   ┌────▼───┐
         │  智谱  │   │DeepSeek│   │ Ollama │
         └────────┘   └────────┘   └────────┘

   ┌──────────────────────────────────────────┐
   │         Observability Layer               │
   │  LangSmith (dev) + OpenTelemetry (prod)  │
   └──────────────────────────────────────────┘
```

---

## 2. 项目结构

```
super_agent/
├── pyproject.toml                  # uv 项目配置
├── docker-compose.yml              # 本地一键部署
├── Dockerfile                      # 应用镜像
│
├── src/
│   └── super_agent/
│       ├── __init__.py
│       ├── main.py                 # LangServe 入口
│       ├── config.py               # Pydantic Settings 统一配置
│       │
│       ├── core/                   # Agent 编排核心
│       │   ├── __init__.py
│       │   ├── orchestrator.py     # 顶层编排入口
│       │   ├── classifier.py       # 任务自动分类器
│       │   ├── router.py           # Router + Specialist 模式
│       │   ├── plan_execute.py     # Plan-and-Execute 模式
│       │   ├── supervisor.py       # Supervisor + Multi-Agent 模式
│       │   └── state.py            # 统一 State 定义
│       │
│       ├── knowledge/              # 知识库模块（第一阶段核心）
│       │   ├── __init__.py
│       │   ├── loaders/            # 文档加载器
│       │   │   ├── __init__.py
│       │   │   ├── base.py         # BaseLoader 接口
│       │   │   ├── pdf.py
│       │   │   ├── word.py
│       │   │   ├── markdown.py
│       │   │   ├── html.py
│       │   │   ├── json_yaml.py
│       │   │   └── csv.py
│       │   ├── chunkers/           # 语义结构切分器
│       │   │   ├── __init__.py
│       │   │   ├── base.py         # BaseChunker 接口
│       │   │   ├── semantic.py     # 语义结构切分（主）
│       │   │   └── fallback.py     # 固定大小兜底切分
│       │   ├── embedders/          # Embedding 可插拔层
│       │   │   ├── __init__.py
│       │   │   ├── base.py         # BaseEmbedder 接口
│       │   │   ├── bge.py          # BGE 本地推理
│       │   │   └── api.py          # 云端 API
│       │   ├── stores/             # 向量库可插拔层
│       │   │   ├── __init__.py
│       │   │   ├── base.py         # BaseVectorStore 接口
│       │   │   ├── chroma.py
│       │   │   └── milvus.py
│       │   ├── retriever.py        # 混合检索 + Rerank
│       │   ├── indexer.py          # 索引构建管线
│       │   └── metadata.py         # metadata 标签体系
│       │
│       ├── memory/                 # 记忆模块
│       │   ├── __init__.py
│       │   ├── base.py             # BaseMemory 接口
│       │   ├── short_term.py       # Redis 短期记忆
│       │   ├── long_term.py        # MySQL 长期记忆
│       │   └── manager.py          # 统一读写入口
│       │
│       ├── tools/                  # 工具层
│       │   ├── __init__.py
│       │   ├── custom/             # 自定义工具
│       │   ├── mcp_client.py       # MCP Client
│       │   ├── mcp_server.py       # MCP Server
│       │   └── skill_loader.py     # AgentSkill 加载器
│       │
│       ├── prompts/                # 提示词编排
│       │   ├── __init__.py
│       │   ├── registry.py         # 提示词注册表
│       │   ├── templates/          # Jinja2 模板文件
│       │   └── versioning.py       # 版本管理
│       │
│       ├── sandbox/                # 沙箱模块
│       │   ├── __init__.py
│       │   ├── docker_manager.py   # 容器生命周期
│       │   └── profiles.py         # 沙箱安全 profile
│       │
│       └── tracing/                # 可观测性
│           ├── __init__.py
│           ├── langsmith.py        # LangSmith 配置
│           └── otel.py             # OpenTelemetry 配置
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── data/
│   ├── raw_docs/                   # 原始文档
│   └── processed/                  # 处理后的数据
│
├── skills/                         # AgentSkill 存放目录
│   └── jenkins-cli-main/
│
└── deploy/                         # 部署配置
    ├── docker/
    │   ├── oneapi/
    │   ├── chroma/
    │   ├── redis/
    │   └── mysql/
    └── otel/
```

---

## 3. 核心模块设计

### 3.1 编排层（core/）

#### 3.1.1 统一 State 定义

```python
class AgentState(TypedDict):
    query: str                           # 用户输入
    session_id: str                      # 会话ID
    mode: Literal["router", "plan_execute", "supervisor"]  # 分类结果
    plan: list[dict] | None             # 执行计划（Plan-Execute 模式）
    current_step: int                    # 当前步骤
    specialist: str | None              # 路由目标（Router 模式）
    messages: list[BaseMessage]          # 对话历史
    context: list[Document]             # 检索上下文
    memory: dict                         # 记忆快照
    artifacts: dict                      # 中间产物（工具输出等）
    trace_id: str                        # 链路追踪ID
```

#### 3.1.2 分类器

```python
# 使用 LLM 做意图分类，输出结构化 JSON
classifier_prompt = """
根据用户问题判断处理模式：
- router: 单一领域、快速回答类问题
- plan_execute: 多步骤、有依赖关系的复杂任务
- supervisor: 需要跨领域协作、多轮审核的任务

用户问题: {query}
会话上下文: {context}

输出 JSON: {"mode": "router|plan_execute|supervisor", "reason": "..."}
"""
```

#### 3.1.3 三种模式对应 LangGraph 结构

**Router 模式**
```
query → classify_intent → specialist_agent → response
```

**Plan-Execute 模式**
```
query → generate_plan → execute_step → replan? → response
                              ↑            │
                              └────────────┘
```

**Supervisor 模式**
```
query → supervisor → delegate_worker → collect → review → response
                          ↑                         │
                          └─────────(retry)─────────┘
```

#### 3.1.4 Orchestrator 顶层图

```python
graph = StateGraph(AgentState)
graph.add_node("classify", classify_node)
graph.add_node("router", router_graph)
graph.add_node("plan_execute", plan_execute_graph)
graph.add_node("supervisor", supervisor_graph)

graph.add_edge(START, "classify")
graph.add_conditional_edges("classify", route_by_mode, {
    "router": "router",
    "plan_execute": "plan_execute",
    "supervisor": "supervisor",
})
graph.add_edge("router", END)
graph.add_edge("plan_execute", END)
graph.add_edge("supervisor", END)
```

---

### 3.2 知识库模块（knowledge/）— 第一阶段核心

#### 3.2.1 模块接口设计

```python
# --- loaders/base.py ---
class BaseLoader(ABC):
    @abstractmethod
    def load(self, source: str) -> list[Document]: ...

    @abstractmethod
    def supported_extensions(self) -> list[str]: ...

# --- chunkers/base.py ---
class BaseChunker(ABC):
    @abstractmethod
    def chunk(self, documents: list[Document],
              max_chunk_size: int = 500,
              overlap_ratio: float | None = None,
              ) -> list[Chunk]: ...

# --- embedders/base.py ---
class BaseEmbedder(ABC):
    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...

# --- stores/base.py ---
class BaseVectorStore(ABC):
    @abstractmethod
    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None: ...

    @abstractmethod
    def search(self, query_embedding: list[float], top_k: int,
               filters: dict | None = None) -> list[SearchResult]: ...

    @abstractmethod
    def delete(self, chunk_ids: list[str]) -> None: ...
```

#### 3.2.2 语义结构切分器设计

切分流程：
```
原始文档
  │
  ├─ PDF/Word → Unstructured 解析 → 结构化元素列表
  │                                  (Title / NarrativeText / Table / CodeBlock)
  ├─ Markdown → 正则 + markdown-it 解析 → 标题树 + 内容块
  ├─ HTML → BeautifulSoup → 去噪 → DOM 树
  └─ JSON/YAML/CSV → 字段级拆分
  │
  ▼
结构化元素列表
  │
  ├─ 跨页合并：相邻同类型元素（表格跨页、代码跨页）合并为完整块
  ├─ 按标题层级分组（同节内容聚合）
  ├─ 表格整块保留
  ├─ 代码块整块保留
  └─ 过大块按句子边界二次切分
       │
       ├─ 标题继承：每个子 chunk 前置完整标题链（不计入 chunk size）
       └─ 句子级重叠：下一个 chunk 包含上一个 chunk 的最后 N 个句子
  │
  ▼
Chunk 列表（带完整 metadata + overlap 标记）
```

#### 3.2.2.1 重叠（Overlap）设计

针对三种上下文断裂场景，采用不同的 overlap 策略：

**场景一：语义切块之间丢失标题上下文 → 标题继承**

```
原文档：
  # 1 运维手册
  ## 1.3 MySQL主从延迟排查
  步骤一：检查 Seconds_Behind_Master 指标。
  步骤二：对比主库 binlog 位点与从库 relay log 位点。
  ...

切分后每个 chunk 自动前置标题链：
  "[1 运维手册 > 1.3 MySQL主从延迟排查]\n步骤一：检查..."
  "[1 运维手册 > 1.3 MySQL主从延迟排查]\n步骤二：对比..."
```

- 标题链不计入 max_chunk_size 限制
- 标题链参与 embedding 计算，增强语义匹配
- metadata 中记录 `heading_path: "1 运维手册 > 1.3 MySQL主从延迟排查"`

**场景二：大段落兜底切分上下文断裂 → 基于 overlap_ratio 的句子级重叠**

```
假设 max_chunk_size=500 tokens，overlap_ratio=0.15

大段落按句子切分为 S1-S10（每句约 100 tokens）：

  Chunk A: [标题继承] + S1 + S2 + S3 + S4 + S5        → 约 500 tokens
  Chunk B: [标题继承] + S5 + S6 + S7 + S8 + S9        → S5 重叠（≈ 500 × 20%，按句子边界对齐）
  Chunk C: [标题继承] + S9 + S10                       → S9 重叠

实际重叠占比 ≈ 15-20%（句子边界对齐导致微小浮动）
```

- 通过 `overlap_ratio` 参数控制（默认 0.15，范围 0.05-0.30），在索引构建时按文档集自定义
- 按 sentences 向上取整对齐到句子边界，宁可多叠一点也不能切断句子
- 不同 chunk_type 有不同的默认比例：

| chunk_type | 默认 overlap_ratio | 说明 |
|-----------|-------------------|------|
| text | 0.15 | 通用文本，平衡上下文与存储 |
| table | 0 | 整块保留，不切不重叠 |
| code | 0 | 整块保留，不切不重叠 |
| list | 0.20 | 列表项关联性强，步骤断列损失大 |

- 重叠部分的 embedding 正常计算，保证语义完整性
- 检索召回时通过 `overlap_source_chunk_id` 字段识别重叠 chunk，避免同一原文区域被重复召回计分

**场景三：PDF 中表格/代码跨页截断 → 跨页合并**

```
PDF 页面 5 末尾：Table 片段（表头 + 前两行）
PDF 页面 6 开头：Table 片段（后三行）

解析时合并：识别相邻同类型元素 → 合并为完整 Table → 整块保留作为一个 chunk
```

- 合并条件：元素类型相同、间距 < 阈值、中间无其他类型元素
- 合并后的 metadata 中 `page_numbers` 记录所有覆盖页码，如 `[5, 6]`

#### 3.2.2.2 Chunk 数据结构

```python
@dataclass
class Chunk:
    id: str                              # chunk 唯一 ID
    content: str                         # 主内容（不含标题继承）
    heading_chain: str                   # 标题链（如 "1 > 1.3 MySQL主从延迟排查"）
    full_text: str                       # 完整文本 = heading_chain + content（用于 embedding）
    metadata: dict                       # 元数据（见需求文档 5.3 节）
    # overlap 相关字段
    is_overlap: bool = False             # 是否为重叠部分
    overlap_source_chunk_id: str | None = None  # 重叠来源 chunk ID
    overlap_ratio: float = 0.15          # 该 chunk 实际使用的重叠比例
    sibling_chunk_ids: list[str] = field(default_factory=list)  # 同父切块的兄弟 chunk
    page_numbers: list[int] = field(default_factory=list)  # chunk 内容覆盖的页码
```

#### 3.2.2.3 Chunker 接口

```python
class BaseChunker(ABC):
    @abstractmethod
    def chunk(self, documents: list[Document],
              max_chunk_size: int = 500,
              overlap_ratio: float | None = None,  # None 时按 chunk_type 使用默认比例
              ) -> list[Chunk]: ...

    def _resolve_overlap_ratio(self, chunk_type: str, user_ratio: float | None) -> float:
        """用户显式指定时用用户的，否则按 chunk_type 查默认表"""
        if user_ratio is not None:
            return max(0.05, min(0.30, user_ratio))  # clamp 到合理范围
        return {"text": 0.15, "table": 0.0, "code": 0.0, "list": 0.20}[chunk_type]
```

#### 3.2.2.3 检索去重

```python
class Retriever:
    def _deduplicate_overlaps(self, results: list[SearchResult]) -> list[SearchResult]:
        """重叠 chunk 召回时去重：只保留得分最高的那个"""
        seen_source = {}
        for r in results:
            source_id = r.chunk.overlap_source_chunk_id or r.chunk.id
            if source_id not in seen_source or r.score > seen_source[source_id].score:
                seen_source[source_id] = r
        return sorted(seen_source.values(), key=lambda x: x.score, reverse=True)
```

#### 3.2.3 检索器设计

```python
class Retriever:
    def __init__(self, store: BaseVectorStore, embedder: BaseEmbedder,
                 reranker: BaseReranker | None = None,
                 use_hybrid: bool = False):
        ...

    def retrieve(self, query: str, top_k: int = 5,
                 filters: dict | None = None) -> list[Chunk]:
        # 1. Query embedding
        query_emb = self.embedder.embed_query(query)

        # 2. 向量检索（带 metadata 前置过滤）
        candidates = self.store.search(query_emb, top_k * 3, filters)

        # 3. 可选：BM25 关键词检索 + 加权融合
        if self.use_hybrid:
            bm25_results = self._bm25_search(query, top_k * 3)
            candidates = self._reciprocal_rank_fusion(candidates, bm25_results)

        # 4. Rerank 重排序
        if self.reranker:
            candidates = self.reranker.rerank(query, candidates, top_k)

        return candidates[:top_k]
```

#### 3.2.4 索引构建管线

```python
class Indexer:
    def build(self, doc_dir: str, filters: dict | None = None):
        """增量构建文档索引"""
        # 1. 扫描文档目录，与已有索引比对，识别新增/变更文件
        # 2. 根据文件扩展名选择对应 Loader
        # 3. 加载 → 切分 → 生成 embedding → 写入向量库
        # 4. 记录处理状态（文件 hash + 处理时间）

    def rebuild(self, doc_dir: str):
        """全量重建索引"""
        # 清空向量库 → 全量处理所有文档
```

#### 3.2.5 metadata 过滤查询示例

```python
# 查找 SRE 部门的 MySQL 故障处理文档
results = retriever.retrieve(
    query="MySQL主从延迟排查步骤",
    filters={
        "department": "SRE",
        "doc_type": "runbook",
        "topic_tags": {"$contains": "mysql"},
        "severity": {"$in": ["critical", "high"]}
    }
)
```

---

### 3.3 记忆模块（memory/）

#### 3.3.1 短期记忆（Redis）

```python
class ShortTermMemory:
    """会话级记忆，TTL 自动过期"""

    def __init__(self, redis_client: Redis, ttl: int = 3600):
        self.redis = redis_client
        self.ttl = ttl

    def save(self, session_id: str, messages: list[BaseMessage]) -> None:
        key = f"memory:short:{session_id}"
        self.redis.setex(key, self.ttl, json.dumps(serialize_messages(messages)))

    def load(self, session_id: str) -> list[BaseMessage]:
        key = f"memory:short:{session_id}"
        data = self.redis.get(key)
        return deserialize_messages(data) if data else []
```

#### 3.3.2 长期记忆（MySQL）

```python
class LongTermMemory:
    """持久化记忆，跨会话经验积累"""

    def __init__(self, engine: Engine):
        self.engine = engine

    def save(self, user_id: str, key: str, value: str,
             metadata: dict | None = None) -> None:
        # INSERT INTO memories (user_id, key, value, metadata, created_at)
        # metadata 存为 JSON

    def search(self, user_id: str, query: str,
               limit: int = 10) -> list[MemoryEntry]:
        # SELECT ... WHERE user_id = ?
        # 可选：对 value 做 LIKE 或全文检索
```

**MySQL 表结构：**

```sql
CREATE TABLE memories (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id     VARCHAR(64) NOT NULL,
    session_id  VARCHAR(64),
    key         VARCHAR(255) NOT NULL,
    value       TEXT NOT NULL,
    metadata    JSON,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id),
    INDEX idx_user_key (user_id, key),
    INDEX idx_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

---

### 3.4 工具层（tools/）

#### 3.4.1 自定义工具

```python
from langchain_core.tools import BaseTool

class SystemHealthCheckTool(BaseTool):
    name: str = "system_health_check"
    description: str = "检查指定系统的健康状态"

    def _run(self, system_name: str) -> str:
        # 实现逻辑
        ...

    async def _arun(self, system_name: str) -> str:
        ...
```

#### 3.4.2 MCP Client

```python
class MCPClient:
    """连接外部 MCP Server，自动发现工具并转为 LangChain BaseTool"""

    async def connect(self, server_url: str) -> list[BaseTool]:
        # 1. 建立 SSE/stdio 连接
        # 2. 调用 tools/list 发现可用工具
        # 3. 每个 MCP tool 包装为 LangChain BaseTool
        # 4. 返回工具列表

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        # 调用 tools/call
        ...
```

#### 3.4.3 MCP Server

```python
class MCPServer:
    """将系统内部能力暴露为 MCP 工具"""

    tools = [
        {"name": "knowledge_search", "description": "检索知识库", ...},
        {"name": "memory_query", "description": "查询长期记忆", ...},
    ]

    async def handle_request(self, method: str, params: dict) -> Any:
        # JSON-RPC 分发
        ...
```

#### 3.4.4 AgentSkill 加载器

```python
class SkillLoader:
    """加载 skills/ 目录下的外部 AgentSkill"""

    def load(self, skill_dir: str) -> list[BaseTool]:
        # 1. 读取 skill_dir/.claude-plugin/plugin.json
        # 2. 解析 skills 定义
        # 3. 将每个 skill 包装为 LangChain BaseTool
        # 4. 返回工具列表
```

---

### 3.5 提示词编排（prompts/）

#### 3.5.1 模板结构

```
prompts/templates/
├── system/                  # 系统提示词
│   ├── router.j2
│   ├── planner.j2
│   └── supervisor.j2
├── specialist/              # 各领域 Specialist 提示词
│   ├── sre.j2
│   ├── dba.j2
│   └── network.j2
└── tools/                   # 工具使用提示词
    ├── knowledge_search.j2
    └── code_exec.j2
```

#### 3.5.2 注册表

```python
class PromptRegistry:
    def get(self, name: str, version: str = "latest") -> str:
        # 从模板文件渲染，支持版本回退

    def reload(self) -> None:
        # 热加载：重新扫描模板目录，无需重启

    def list_versions(self, name: str) -> list[str]:
        # 列出某个提示词的所有版本
```

---

### 3.6 沙箱模块（sandbox/）

#### 3.6.1 Docker Manager

```python
class DockerSandboxManager:
    async def execute(self, profile: SandboxProfile,
                      command: str,
                      timeout: int | None = None) -> SandboxResult:
        # 1. 从 profile 构建容器配置
        # 2. docker run（限制网络/CPU/内存/文件系统）
        # 3. 等待执行完成或超时
        # 4. 收集 stdout/stderr/exit_code
        # 5. 强制清理容器
        ...
```

#### 3.6.2 安全 Profile 定义

```python
@dataclass
class SandboxProfile:
    name: str
    image: str                           # 基础镜像
    network: str | None                  # 网络模式（None=无网络）
    read_only_paths: list[str]           # 只读挂载
    read_write_paths: list[str]          # 读写挂载
    cpu_limit: float                     # CPU 核数
    memory_limit: str                    # 内存限制
    timeout: int                         # 执行超时（秒）
    allowed_env_vars: list[str]          # 允许传入的环境变量名

PROFILES = {
    "code-exec": SandboxProfile(
        name="code-exec", image="super-agent/sandbox:python",
        network=None, read_only_paths=["/workspace"],
        cpu_limit=2.0, memory_limit="1g", timeout=60,
        allowed_env_vars=["ONEAPI_BASE_URL", "ONEAPI_API_KEY"],
    ),
    "ops-isolated": SandboxProfile(...),
    "skill-sandbox": SandboxProfile(...),
    "data-pipeline": SandboxProfile(...),
}
```

---

### 3.7 可观测性（tracing/）

#### 3.7.1 双重采集架构

```
LangChain Callbacks
      │
      ├──→ LangSmithHandler     (开发调试)
      │      └── LangSmith UI: 查看 input/output/tokens/latency
      │
      └──→ OTelLangChainHandler (生产监控)
             └── OpenTelemetry Collector
                    └── Jaeger: 链路追踪 + 告警
```

#### 3.7.2 Trace 传播

每个请求入口生成 `trace_id`，通过 `AgentState.trace_id` 在整个 LangGraph 执行链中传播。所有日志、回调、数据库写入均携带此 ID，实现端到端关联。

---

## 4. 配置管理

### 4.1 设计原则

- **分类聚合**：按业务领域拆分子配置类，避免单个巨型 Settings 类
- **优先级明确**：环境变量 > .env 文件 > 默认值
- **多环境支持**：通过 `.env.dev` / `.env.prod` 切换环境
- **敏感信息隔离**：API Key 等敏感项不在代码中硬编码，必须通过环境变量或 .env 注入
- **启动校验**：服务启动时校验必要配置项的合法性，尽早失败

### 4.2 配置分层结构

```python
# --- config.py ---

class LLMConfig(BaseSettings):
    """LLM 资源配置 — 通过 OneAPI 统一代理"""
    oneapi_base_url: str = "http://localhost:3000/v1"
    oneapi_api_key: str = ""                # 必须通过环境变量注入
    default_model: str = "gpt-4o"
    default_temperature: float = 0.7
    max_tokens: int = 4096
    request_timeout: int = 60               # 秒

    # 按 Agent 模式覆盖模型
    router_model: str = ""                  # 为空则用 default_model
    planner_model: str = ""
    supervisor_model: str = ""
    code_model: str = ""                    # 代码执行场景可用更便宜模型

    model_config = SettingsConfigDict(env_prefix="SA_LLM_")


class EmbeddingConfig(BaseSettings):
    """Embedding 模型配置 — 可插拔"""
    provider: Literal["bge", "api"] = "bge"
    # BGE 本地
    bge_model_name: str = "BAAI/bge-large-zh-v1.5"
    bge_device: str = "cpu"                 # cpu / cuda
    bge_max_batch_size: int = 32
    # 云端 API
    api_url: str = ""
    api_key: str = ""
    api_model: str = ""

    model_config = SettingsConfigDict(env_prefix="SA_EMBEDDING_")


class VectorStoreConfig(BaseSettings):
    """向量数据库配置 — 可插拔"""
    provider: Literal["chroma", "milvus"] = "chroma"
    # Chroma
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    chroma_persist_dir: str = "./data/chroma"
    # Milvus
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "super_agent_docs"
    # 通用
    default_top_k: int = 5

    model_config = SettingsConfigDict(env_prefix="SA_VECTOR_")


class RedisConfig(BaseSettings):
    """Redis 配置 — 短期记忆 + 缓存"""
    url: str = "redis://localhost:6379"
    db: int = 0
    password: str = ""
    short_memory_ttl: int = 3600           # 短期记忆 TTL（秒）
    pool_size: int = 10

    model_config = SettingsConfigDict(env_prefix="SA_REDIS_")


class MySQLConfig(BaseSettings):
    """MySQL 配置 — 长期记忆 + 元数据"""
    host: str = "localhost"
    port: int = 3306
    username: str = "root"
    password: str = ""                      # 必须通过环境变量注入
    database: str = "super_agent"
    pool_size: int = 10
    echo_sql: bool = False                  # 开发环境调试用

    @property
    def dsn(self) -> str:
        return f"mysql+asyncmy://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}"

    model_config = SettingsConfigDict(env_prefix="SA_MYSQL_")


class SandboxConfig(BaseSettings):
    """Docker 沙箱配置"""
    docker_host: str = "unix:///var/run/docker.sock"
    default_profile: str = "code-exec"
    cleanup_on_exit: bool = True
    max_concurrent_containers: int = 5

    model_config = SettingsConfigDict(env_prefix="SA_SANDBOX_")


class TracingConfig(BaseSettings):
    """可观测性配置"""
    # LangSmith（开发期）
    langsmith_api_key: str = ""
    langsmith_project: str = "super-agent"
    enable_langsmith: bool = True
    # OpenTelemetry（生产期）
    enable_otel: bool = False
    otel_exporter: str = "http://localhost:4317"
    otel_service_name: str = "super-agent"

    model_config = SettingsConfigDict(env_prefix="SA_TRACING_")


class ServerConfig(BaseSettings):
    """LangServe 服务配置"""
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    cors_origins: list[str] = ["*"]
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    model_config = SettingsConfigDict(env_prefix="SA_SERVER_")


class Settings(BaseSettings):
    """顶层配置入口 — 聚合所有子配置"""
    llm: LLMConfig = LLMConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    vector_store: VectorStoreConfig = VectorStoreConfig()
    redis: RedisConfig = RedisConfig()
    mysql: MySQLConfig = MySQLConfig()
    sandbox: SandboxConfig = SandboxConfig()
    tracing: TracingConfig = TracingConfig()
    server: ServerConfig = ServerConfig()

    # 全局环境标识
    env: Literal["dev", "prod"] = "dev"

    model_config = SettingsConfigDict(env_prefix="SA_", env_file=".env")


# 全局单例
settings = Settings()
```

### 4.3 环境变量映射规则

| 子配置 | 环境变量前缀 | 示例 |
|-------|------------|------|
| LLM | `SA_LLM_` | `SA_LLM_ONEAPI_API_KEY=sk-xxx` |
| Embedding | `SA_EMBEDDING_` | `SA_EMBEDDING_PROVIDER=api` |
| 向量库 | `SA_VECTOR_` | `SA_VECTOR_PROVIDER=milvus` |
| Redis | `SA_REDIS_` | `SA_REDIS_URL=redis://cache:6379` |
| MySQL | `SA_MYSQL_` | `SA_MYSQL_PASSWORD=secret` |
| 沙箱 | `SA_SANDBOX_` | `SA_SANDBOX_DOCKER_HOST=tcp://docker:2376` |
| 追踪 | `SA_TRACING_` | `SA_TRACING_ENABLE_OTEL=true` |
| 服务 | `SA_SERVER_` | `SA_SERVER_PORT=8080` |
| 全局 | `SA_` | `SA_ENV=prod` |

### 4.4 多环境文件支持

```
.env                  # 默认（所有人通用、不提交 git）
.env.dev              # 开发环境示例（提交 git，无敏感值）
.env.prod             # 生产环境模板（提交 git，占位符）
.env.local            # 个人本地覆盖（不提交 git）
```

加载优先级：`.env.local` > `.env.{env}` > `.env`

### 4.5 .env.dev 示例

```bash
# 全局
SA_ENV=dev

# LLM（OneAPI）
SA_LLM_ONEAPI_BASE_URL=http://localhost:3000/v1
SA_LLM_ONEAPI_API_KEY=sk-your-dev-key
SA_LLM_DEFAULT_MODEL=gpt-4o

# Embedding
SA_EMBEDDING_PROVIDER=bge
SA_EMBEDDING_BGE_MODEL_NAME=BAAI/bge-large-zh-v1.5
SA_EMBEDDING_BGE_DEVICE=cpu

# 向量库
SA_VECTOR_PROVIDER=chroma
SA_VECTOR_CHROMA_PERSIST_DIR=./data/chroma

# Redis
SA_REDIS_URL=redis://localhost:6379
SA_REDIS_SHORT_MEMORY_TTL=3600

# MySQL
SA_MYSQL_HOST=localhost
SA_MYSQL_PORT=3306
SA_MYSQL_USERNAME=root
SA_MYSQL_PASSWORD=devpassword
SA_MYSQL_DATABASE=super_agent

# 沙箱
SA_SANDBOX_DOCKER_HOST=unix:///var/run/docker.sock

# 追踪
SA_TRACING_ENABLE_LANGSMITH=true
SA_TRACING_LANGSMITH_API_KEY=lsv2_pt_xxx
SA_TRACING_ENABLE_OTEL=false

# 服务
SA_SERVER_PORT=8000
SA_SERVER_LOG_LEVEL=DEBUG
```

### 4.6 启动校验

```python
def validate_settings(s: Settings) -> None:
    """服务启动时校验，配置有问题立即报错而非运行时才暴露"""
    errors = []
    if s.env == "prod":
        if not s.llm.oneapi_api_key:
            errors.append("SA_LLM_ONEAPI_API_KEY is required in production")
        if not s.mysql.password:
            errors.append("SA_MYSQL_PASSWORD is required in production")
        if not s.redis.password:
            errors.append("SA_REDIS_PASSWORD is required in production")
        if s.server.cors_origins == ["*"]:
            errors.append("CORS wildcard not allowed in production")
        if s.server.log_level == "DEBUG":
            errors.append("DEBUG log level not recommended in production")
    if s.embedding.provider == "bge" and s.embedding.bge_device == "cuda":
        # 提示但不阻断
        logger.warning("BGE CUDA mode requested — ensure GPU is available")
    if errors:
        raise ConfigurationError("\n".join(errors))
```

### 4.7 各模块使用方式

```python
# knowledge/embedders/bge.py — 只消费自己关心的配置
from super_agent.config import settings

class BGEEmbedder(BaseEmbedder):
    def __init__(self):
        cfg = settings.embedding
        self.model = SentenceTransformer(cfg.bge_model_name, device=cfg.bge_device)
        self.batch_size = cfg.bge_max_batch_size

# core/orchestrator.py — 按 Agent 模式选择模型
from super_agent.config import settings

def get_model_for_mode(mode: str) -> ChatOpenAI:
    cfg = settings.llm
    model_name = {
        "router": cfg.router_model or cfg.default_model,
        "plan_execute": cfg.planner_model or cfg.default_model,
        "supervisor": cfg.supervisor_model or cfg.default_model,
    }[mode]
    return ChatOpenAI(
        base_url=cfg.oneapi_base_url,
        api_key=cfg.oneapi_api_key,
        model=model_name,
        temperature=cfg.default_temperature,
        max_tokens=cfg.max_tokens,
        timeout=cfg.request_timeout,
    )
```

---

## 5. Docker Compose 部署设计（第一阶段）

```yaml
services:
  app:
    build: .
    ports: ["8000:8000"]
    depends_on: [chroma, redis, mysql, oneapi]
    env_file: .env
    volumes:
      - ./data:/app/data
      - ./skills:/app/skills
      - /var/run/docker.sock:/var/run/docker.sock  # 沙箱

  chroma:
    image: chromadb/chroma:latest
    ports: ["8001:8000"]
    volumes:
      - chroma-data:/chroma/chroma

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  mysql:
    image: mysql:8.0
    ports: ["3306:3306"]
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: super_agent
    volumes:
      - mysql-data:/var/lib/mysql

  oneapi:
    image: justsong/one-api:latest
    ports: ["3000:3000"]
    volumes:
      - oneapi-data:/data

  jaeger:
    image: jaegertracing/all-in-one:latest
    ports: ["16686:16686", "4317:4317"]

volumes:
  chroma-data:
  mysql-data:
  oneapi-data:
```

---

## 6. uv 项目配置

```toml
[project]
name = "super-agent"
version = "0.1.0"
description = "企业级 AI 应用开发平台"
requires-python = ">=3.12"
dependencies = [
    "langchain>=0.3",
    "langgraph>=0.2",
    "langserve[server]>=0.3",
    "langsmith>=0.1",
    "langchain-community>=0.3",
    "langchain-openai>=0.2",
    "langchain-chroma>=0.2",
    "langchain-milvus>=0.1",
    "pydantic-settings>=2.0",
    "jinja2>=3.1",
    "unstructured[all-docs]>=0.15",
    "pymupdf>=1.24",
    "python-docx>=1.1",
    "beautifulsoup4>=4.12",
    "sentence-transformers>=3.0",
    "FlagEmbedding>=1.2",
    "redis>=5.0",
    "sqlalchemy[asyncio]>=2.0",
    "asyncmy>=0.2",
    "docker>=7.0",
    "opentelemetry-api>=1.20",
    "opentelemetry-sdk>=1.20",
    "opentelemetry-exporter-otlp>=1.20",
    "fastapi>=0.111",
    "uvicorn>=0.30",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.5",
]

[project.scripts]
super-agent = "super_agent.main:main"

[tool.uv]
dev-dependencies = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.5",
]

[tool.ruff]
line-length = 120
target-version = "py312"
```

---

## 7. 数据流设计

### 7.1 知识库索引流程（第一阶段核心）

```
raw_docs/                    ┌──────────┐    ┌──────────┐    ┌──────────┐
  ├─ runbook.pdf         ──→ │  Loader   │──→ │ Chunker  │──→ │ Embedder │──→ Chroma
  ├─ api_doc.md          ──→ │          │    │          │    │          │
  ├─ policy.docx         ──→ │ (按扩展名 │    │(语义结构 │    │(BGE/API │
  ├─ alerts.json         ──→ │  选择)    │    │ 切分)    │    │ 可切换)  │
  └─ metrics.csv         ──→ │          │    │          │    │          │
                            └──────────┘    └──────────┘    └──────────┘
                                                                      │
                                                              ┌───────▼───────┐
                                                              │  Vector Store  │
                                                              │  + metadata    │
                                                              └───────────────┘
```

### 7.2 RAG 查询流程

```
用户 query
    │
    ▼
[Embedder] → query_embedding
    │
    ▼
[VectorStore.search] → Top-K 候选（带 metadata 过滤）
    │
    ▼ (可选)
[BM25 搜索] → Top-K 关键词结果
    │
    ▼ (加权融合)
[RRF 融合] → 融合后候选
    │
    ▼ (可选)
[Reranker] → 重排序 Top-N
    │
    ▼
[构造 context] → 喂给 LLM 生成回答
```

### 7.3 Agent 请求全链路

```
HTTP Request
    │ (trace_id 生成)
    ▼
LangServe → Orchestrator → Classifier
    │                         │
    │                    route_by_mode
    │                    ┌────┼────────┐
    │                    ▼    ▼        ▼
    │               Router  PlanExec  Supervisor
    │                    │    │        │
    │                    ▼    ▼        ▼
    │               Specialist / Worker Agents
    │                    │    │        │
    │                    ▼    ▼        ▼
    │               Tools / Knowledge / Memory
    │
    ▼
HTTP Response (附带 trace_id)
```

---

## 8. 第一阶段交付清单

| 序号 | 交付物 | 验收标准 |
|------|-------|---------|
| 1 | 项目脚手架（uv + 目录结构 + pyproject.toml） | `uv run super-agent` 可启动 |
| 2 | 配置管理模块（config.py + .env 模板） | 所有配置项可环境变量覆盖 |
| 3 | 文档加载器（6 种格式） | 每种格式有单元测试 |
| 4 | 语义结构切分器 | 切分结果保留标题层级，表格/代码块不拆散 |
| 5 | Embedding 可插拔层（BGE 本地 + API） | 配置切换无需改代码 |
| 6 | 向量库可插拔层（Chroma + Milvus） | 配置切换无需改代码 |
| 7 | 检索器（向量 + metadata 过滤 + Rerank） | Top-5 召回率 >= 90% |
| 8 | 索引构建管线（增量 + 全量） | 重复执行不产生重复索引 |
| 9 | Docker Compose 部署文件 | `docker compose up` 一键启动全部依赖 |
| 10 | 基础 RAG API（LangServe） | POST /rag/query 可用 |
