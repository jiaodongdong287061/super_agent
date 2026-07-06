# Super Agent 需求分析文档

## 1. 项目概述

### 1.1 项目名称
Super Agent — 企业级 AI 应用开发平台

### 1.2 项目目标
构建一个兼顾**个人学习**与**企业生产级部署**的 AI 应用开发平台，以 LangChain 生态为核心，提供从知识库构建、Agent 编排到工具集成的全栈能力。

### 1.3 项目定位
- **学习侧**：模块化设计，每个子系统可独立学习、调试和实验
- **生产侧**：可插拔组件、全链路追踪、沙箱隔离，满足企业部署的稳定性与安全性要求

---

## 2. 总体需求

### 2.1 功能需求总览

| 编号 | 需求领域 | 描述 |
|------|---------|------|
| F1 | 知识库（RAG） | 本地高质量知识库，支持多格式文档摄入、语义结构切分、metadata 多标签检索 |
| F2 | Agent 编排 | 三种模式自动路由：Router+Specialist / Plan-Execute / Supervisor+Multi-Agent |
| F3 | 记忆系统 | 短期记忆（Redis）+ 长期记忆（MySQL），支持会话上下文和经验持久化 |
| F4 | 提示词编排 | 系统/用户提示词的模板化管理、版本控制、运行时热加载 |
| F5 | 工具生态 | 自定义工具 + MCP Server/Client + AgentSkill 外部技能加载 |
| F6 | 沙箱隔离 | 基于 Docker 的安全执行环境，覆盖代码执行、运维操作、外部 Skill、数据管道四种场景 |
| F7 | 可观测性 | 全链路追踪，开发期用 LangSmith，生产期用 OpenTelemetry + Jaeger |
| F8 | 工作流编排 | 基于 LangGraph 的可视化/代码化工作流定义与执行 |

### 2.2 非功能需求

| 编号 | 类别 | 描述 |
|------|------|------|
| NF1 | 可扩展性 | 向量数据库、Embedding 模型、LLM 供应商均为可插拔设计 |
| NF2 | 可观测性 | 每个请求的处理链路可追踪到 Agent 决策、工具调用、检索召回的每个节点 |
| NF3 | 安全性 | 外部代码和工具在 Docker 沙箱中隔离运行，不同场景使用不同安全等级的沙箱 profile |
| NF4 | 易部署性 | 第一阶段纯本地 Docker Compose 一键启动 |
| NF5 | 可维护性 | 模块间通过接口解耦，每个模块可独立测试和替换 |
| NF6 | 包管理 | 使用 uv 进行项目打包、编译和运行 |

---

## 3. 分阶段交付计划

### 3.1 第一阶段（当前）：本地高质量知识库搭建

**目标**：构建高召回率、支持 metadata 多标签检索的本地知识库

**交付内容**：
- 文档摄入管线：支持 PDF/Word/TXT/Markdown/HTML/JSON/CSV
- 语义结构切分器：按文档结构（标题/段落/表格/代码块）保留语义完整性
- Embedding 可插拔层：默认 BGE-large-zh-v1.5 本地推理，可切换云端 API
- 向量存储可插拔层：本地开发用 Chroma，可切换 Milvus
- 检索器：向量相似度 + metadata 过滤混合检索 + Rerank 重排序
- metadata 标签体系：支持文档类型、来源、部门、主题、时间等多维度标签
- Docker Compose 本地一键部署

**成功标准**：
- 对 IT 运维文档的 Top-5 召回率 >= 90%
- metadata 多标签组合过滤查询响应时间 < 500ms（万级文档）
- 支持至少 6 种文档格式摄入

### 3.2 第二阶段：Agent 编排与记忆系统

**目标**：实现三种 Agent 编排模式 + 完整记忆系统

**交付内容**：
- 任务自动分类器
- Router + Specialist Agent 模式
- Plan-and-Execute 模式
- Supervisor + Multi-Agent 模式
- 短期记忆（Redis）、长期记忆（MySQL）
- 提示词编排引擎
- LangServe 对外 API

### 3.3 第三阶段：工具生态与生产化

**目标**：完善工具集成和生产级能力

**交付内容**：
- MCP Server / Client 实现
- AgentSkill 加载器
- 自定义工具框架
- Docker 沙箱（四种 profile）
- OpenTelemetry + Jaeger 生产链路追踪
- 工作流编排 UI

---

## 4. 技术选型

| 维度 | 选型 | 说明 |
|------|------|------|
| 开发语言 | Python 3.12+ | 生态丰富，LangChain 首选语言 |
| 包管理 | uv | 高性能 Python 包管理器 |
| LLM 框架 | LangChain + LangGraph + LangServe + LangSmith | 全栈 LangChain 生态 |
| LLM 接入 | OneAPI（OpenAI 兼容接口） | 统一代理多 LLM 供应商 |
| 向量数据库 | Chroma（开发）/ Milvus（生产） | 可插拔设计 |
| Embedding | BGE-large-zh-v1.5（本地）/ 云端 API | 可插拔设计 |
| 短期记忆 | Redis | 会话级 TTL，低延迟 |
| 长期记忆 | MySQL | 持久化，复用现有运维体系 |
| 沙箱 | Docker | 成熟的容器隔离方案 |
| 开发期追踪 | LangSmith | 开箱即用的 LangChain 可视化调试 |
| 生产期追踪 | OpenTelemetry + Jaeger | 标准协议，可对接各类后端 |
| 配置管理 | Pydantic Settings | 类型安全的配置管理 |
| 部署 | Docker Compose（第一阶段） | 本地一键启动 |

---

## 5. 知识库详细需求

### 5.1 文档摄入

| 文档类型 | 加载策略 | 特殊处理 |
|---------|---------|---------|
| PDF | PyMuPDF / Unstructured | 表格识别、OCR 扫描件 |
| Word | python-docx / Unstructured | 保留标题层级结构 |
| TXT | 原生读取 | 编码自动检测（UTF-8/GBK） |
| Markdown | UnstructuredMarkdownLoader | 保留标题层级和代码块标记 |
| HTML | BeautifulSoup / Unstructured | 去除导航栏/广告等噪音 |
| JSON/YAML | 原生解析 | 按 key 拆分为独立 chunk |
| CSV | Pandas | 每行或每组行为一个 chunk |

### 5.2 语义结构切分

**核心原则**：按文档的语义结构切分，而非固定 token 数

- **标题层级**：一级标题下的内容作为一个整体，二级标题递归切分
- **段落**：段落是最小语义单元，不被跨段落切断
- **表格**：整张表格作为一个 chunk，不被分行拆散；PDF 跨页表格自动合并
- **代码块**：完整的函数/类作为一个 chunk；PDF 跨页代码自动合并
- **兜底**：对超过最大 chunk size 的段落，按句子边界二次切分，并在 metadata 中标记"parent_chunk_id"保持关联
- **标题继承**：切分后每个 chunk 前置完整的标题链（如"1 运维手册 > 1.3 MySQL主从延迟"），标题链参与 embedding 但不计入 chunk size 限制
- **句子级重叠**：兜底切分时，基于 `overlap_ratio`（默认 0.15，范围 0.05-0.30，可在索引构建时按文档集自定义）控制重叠比例，按句子边界向上取整对齐；不同 chunk_type 默认比例不同（text=0.15, table=0, code=0, list=0.20）；重叠部分在 metadata 中标记 `is_overlap=True` 和 `overlap_source_chunk_id`，检索召回时自动去重

### 5.3 Metadata 标签体系

每个 chunk 携带以下 metadata 字段：

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| doc_source | string | 文档来源标识 | "confluence" / "gitbook" / "local_file" |
| doc_type | string | 文档类型 | "runbook" / "api_doc" / "policy" / "alert_rule" |
| department | string | 所属部门 | "SRE" / "DBA" / "Network" |
| topic_tags | list[string] | 主题标签（多值，用户自定义） | ["mysql", "backup", "recovery"] |
| system_name | string | 关联系统 | "crm-prod" / "jenkins-ci" |
| severity | string | 重要程度 | "critical" / "high" / "normal" |
| created_at | datetime | 文档创建时间 | "2026-01-15" |
| updated_at | datetime | 文档更新时间 | "2026-06-10" |
| chunk_type | string | chunk 内容类型 | "text" / "table" / "code" / "list" |
| parent_chunk_id | string | 父 chunk ID（切分关联） | "uuid-xxx" |
| page_numbers | list[int] | chunk 内容覆盖的页码 | [5] / [5, 6] |

支持任意组合过滤：如 `doc_type="runbook" AND topic_tags CONTAINS "mysql" AND severity="critical"`

#### 5.3.1 topic_tags 标签来源

标签由三种方式生成，优先级：手动标注 > 目录继承 > LLM 自动打标

| 方式 | 说明 | 适用场景 |
|------|------|---------|
| 手动标注 | 文档摄入时通过 API 参数传入 | 制度文档、核心 runbook 等重要文档 |
| 目录继承 | 按文件目录结构自动推断标签 | `raw_docs/SRE/mysql/主从延迟排查.md` → 继承 `["SRE", "mysql"]` |
| LLM 自动打标 | 对无目录语义的文档，由 LLM 分析内容后生成标签 | 散落文件、历史存量文档 |

| 方式 | 示例 |
|------|------|
| 手动标注 | `indexer.build(doc_dir, tags=["mysql", "backup", "recovery"])` |
| 目录继承 | `raw_docs/SRE/mysql/主从延迟排查.md` → `topic_tags=["SRE", "mysql"]` |
| LLM 打标 | LLM 分析内容后输出 `topic_tags=["mysql", "replication", "troubleshooting"]` |

#### 5.3.2 topic_tags 检索支持

`topic_tags` 以 `list[string]` 原生类型存储，各向量库均支持数组字段的包含查询：

| 向量库 | 数组字段类型 | 过滤语法 |
|-------|------------|---------|
| Chroma | 原生 list | `{"topic_tags": {"$contains": "mysql"}}` |
| Milvus | Array<Varchar> | `array_contains(topic_tags, "mysql")` |

支持多标签组合查询：`topic_tags CONTAINS "mysql" AND topic_tags CONTAINS "backup"`

### 5.4 检索策略

1. **向量相似度检索**：用户 query embedding → Top-K 相似 chunk
2. **Metadata 过滤**：支持前置过滤（先过滤再检索）和后置过滤（先检索再过滤），由向量库能力决定
3. **混合检索**：向量相似度 + BM25 关键词检索加权融合（可选开启）
4. **Rerank**：使用 BGE-reranker-v2-m3 对 Top-K 结果重排序，提升 Top 准确率

### 5.5 知识库更新

- 增量索引：仅处理新增/变更文档
- 版本管理：文档更新时标记旧版本，检索默认使用最新版本
- 定期全量重建索引（可配置周期）

---

## 6. Agent 编排详细需求

### 6.1 自动分类器

- 输入：用户 query + 会话上下文
- 输出：路由决策（router / plan_execute / supervisor）
- 分类依据：
  - **Router**：单一领域、快速回答类（如"Jenkins 怎么查看构建日志"）
  - **Plan-Execute**：多步骤、有序依赖类（如"帮我排查 MySQL 主从延迟"）
  - **Supervisor**：需要跨领域协作、多轮审核类（如"制定一套生产变更发布流程"）

### 6.2 Router + Specialist Agent

- Router 根据 query 意图分发到对应领域的 Specialist
- 每个 Specialist 拥有独立的提示词、工具集、知识库范围
- Specialist 之间不通信，各自独立返回结果

### 6.3 Plan-and-Execute

- Planner Agent 生成步骤计划（Step 1, 2, 3...）
- Executor Agent 逐步执行，每步输出结果
- Re-planner 根据执行结果决定是否调整后续计划
- 支持步骤间传递中间结果

### 6.4 Supervisor + Multi-Agent

- Supervisor 全局把控，决定任务分配和结果审核
- 多个 Worker Agent 并行或串行处理子任务
- Supervisor 可要求 Worker 重做或调整输出
- 支持 Worker 之间的信息共享（通过共享 State）

---

## 7. MCP 详细需求

### 7.1 MCP Server

系统对外暴露的能力：
- `knowledge_search`：知识库检索
- `knowledge_index`：文档索引构建
- `tool_execute`：调用系统内部工具
- `memory_query`：查询长期记忆

协议：遵循 MCP 标准协议（JSON-RPC over stdio/SSE）

### 7.2 MCP Client

系统调用外部 MCP Server 的能力：
- 连接管理：维护与外部 MCP Server 的连接池
- 工具发现：自动发现并注册外部工具为 LangChain BaseTool
- 错误处理：超时、连接断开自动重试

---

## 8. 沙箱详细需求

四种隔离场景对应不同安全 profile：

| Profile | 网络访问 | 文件系统 | 资源限制 | 用途 |
|---------|---------|---------|---------|------|
| code-exec | 仅 OneAPI | 只读挂载 /workspace | CPU 2核 / 内存 1GB / 60s 超时 | 用户代码执行 |
| ops-isolated | 特定内网段 | 只读挂载配置 | CPU 2核 / 内存 512MB / 120s 超时 | 运维操作隔离 |
| skill-sandbox | 按需授权 | 隔离 /skill-workspace | CPU 1核 / 内存 512MB / 300s 超时 | 外部 Skill 运行 |
| data-pipeline | 仅内部存储 | 读写 /data 目录 | CPU 4核 / 内存 4GB / 3600s 超时 | 批量数据处理 |

---

## 9. 约束与假设

### 9.1 约束
- 第一阶段不涉及 K8s 部署，纯 Docker Compose
- 第一阶段不实现 Agent 编排和工具生态，仅知识库
- LLM 通过 OneAPI 统一代理，应用层不直接对接各供应商
- Embedding 默认使用本地 BGE 模型，需要 GPU 或接受 CPU 推理速度

### 9.2 假设
- 本地有 Docker 环境可用
- OneAPI 已部署或可在 Docker Compose 中一并部署
- 第一阶段文档规模在万级（1k-10k 篇），不会触发性能瓶颈
- 团队对 Python 和 LangChain 有基本了解
