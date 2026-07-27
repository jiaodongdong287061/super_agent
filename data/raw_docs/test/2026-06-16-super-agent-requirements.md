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
- Guardrails（安全护栏）
- Classifier（任务分类器）
- Single Agent Runtime（单 Agent 执行引擎）
- Skills / RAG / Tools 能力注入层
- PlanExecute 复杂任务管线
- MCP Tools 外部工具集成
- Human Approval Gateway（人工审批网关）
- Context 管理 + 会话管理
- 短期记忆（Redis）+ 长期记忆（MySQL）
- Agent 可观测性（追踪 + 审计 + 指标）
- 提示词编排引擎

实际企业级不会使用单一模式，通常是如下架构模式：
                 User
                  |
                  v

             Supervisor
                  |
                  v

              Router

        +---------+----------+
        |         |          |
        v         v          v

    RAG Agent  Tool Agent  Workflow Agent


                  |
                  v

          Plan-and-Execute


                  |
                  v

             Tools

也就是：

Supervisor
    |
    |
 Router
    |
 Specialist Agents
    |
 Planner
    |
 Tools

---

#### 3.2.1 架构选型：从多 Agent 到单 Agent Runtime

##### 初始设计（多 Agent 架构）

以上是企业级 Agent 的经典分层模型，但经过设计评审和实际场景推演后，我们选择了另一种路线。

##### 最终采用（单 Agent Runtime 架构）

```
                 User
                  |
                  |
              Guardrails
                  |
                  |
              Classifier
                  |
                  |
          Single Agent Runtime
                  |
                  |
      ┌───────────┼───────────┐
      |           |           |
    Skills      RAG         Tools
      |           |           |
      └───────────┼───────────┘
                  |
             PlanExecute
          (Complex Task Only)
                  |
              MCP Tools
                  |
        Human Approval Gateway
                  |
                  |
             Execution
```

##### 两种方案对比

| 对比维度 | 多 Agent 架构（原方案） | 单 Agent Runtime（现方案） |
|---------|----------------------|--------------------------|
| **Agent 划分方式** | 写死三类 Specialist（RAG/Tool/Workflow） | 一个 Agent，**按需加载** 能力 |
| **加新领域** | 加新 Agent + 新路由规则 | 加新 Tool + 新知识库，不改 Agent |
| **执行路径** | Supervisor → Router → Specialist → Planner → Tools（5 层） | Guardrails → Classifier → Runtime → Tools（3-4 层） |
| **PlanExecute** | 独立 Agent 模式，始终可用 | Runtime 的**可选管线**，仅复杂任务启用 |
| **上下文管理** | 多 Agent 各自独立上下文，需要共享机制 | 单上下文统一管理，不存在跨 Agent 共享问题 |
| **路由决策** | Router + Supervisor 两次重复分类 | Classifier 一次分类，Runtime 直接执行 |
| **灵活度** | 低：Agent 类型和职责在架构层面确定 | 高：能力由配置决定，架构层面不做假设 |
| **实现复杂度** | 高：需要实现多 Agent 通信、状态同步 | 低：单循环 + 能力注入 |

##### 选择单 Agent 架构的原因

**1. Agent 的专业度 = 所配的能力，而非写死的类型**

Agent 会什么，取决于运行时挂载了什么 Tools、Skills、RAG 知识库和 MCP 工具。加一个新领域的支持 = 加工具 + 加知识库，不需要新增 Agent 类型，也不需要改路由规则。这比预定义 Specialist RAG/Tool/Workflow 三类 Agent 灵活得多。

**2. 架构链路过深**

原方案 5 层（Supervisor → Router → Specialist → Planner → Tools），每个请求至少过 3 层 LLM 调用。一个简单的 "查 Jenkins 构建日志" 也要走完完整链路，延迟和 Token 成本不必要。

而单 Agent 方案：
- 纯问答 → 直接 LLM 回答，1 次 LLM 调用
- 知识查询 → RAG 检索 → LLM 回答，2 次
- 工具调用 → ReAct 循环，N 次 LLM 调用
- 复杂任务 → 触发 PlanExecute 管线

按需选择路径，不为简单请求付出复杂代价。

**3. Router 和 Supervisor 职责重叠**

在多 Agent 方案中，Supervisor 负责"全局把控 + 任务分配 + 结果审核"，Router 负责"意图分发"。实际执行中 Supervisor 天然包含路由职能，两者分离会导致两次重复分类判断，增加延迟和出错概率。

单 Agent 方案由 Classifier 一次性完成意图判断，Runtime 直接执行，不存在重叠。

**4. 没有跨 Agent 通信的开销**

多 Agent 方案中 Agent 之间需要共享状态、传递结果，增加了实现复杂度和出错面。单 Agent 方案所有能力在同一个 Runtime 内调度，状态统一管理，不存在通信问题。

##### 三者的关系

原方案的三种模式在单 Agent Runtime 中对应不同的执行路径：

```
原方案模式              单 Agent Runtime 中的等价路径
─────────────────────────────────────────────────
Router + Specialist     → Runtime 加载匹配的 Tools/RAG/Skills → ReAct 循环
Plan-and-Execute        → Runtime 的 PlanExecute 管线（仅复杂任务启用）
Supervisor + Multi-Agent → 当前范围外，Phase 3 通过 Workflow 编排实现
```

Supervisor + Multi-Agent 模式需要的跨 Agent 协作能力，本质上是一个工作流编排问题，归入 Phase 3 的工作流编排范围更合理。Phase 2 聚焦"单 Agent 如何高效完成任务"这一核心问题。

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
- **Embedding 语义边界检测**（v2 增强）：在句子级切分过程中，将句子列表批量送入 Embedding API 获取向量，计算相邻句子的余弦相似度。以"均值 − 标准差"为阈值标记语义断点（topic shift），在断点处优先切分 chunk。内置保护规则：不足 5 句不检测、断点间距小于 3 句时合并、断点数量不超过总句数的 40%。无 Embedder 时退回纯规则切分，完全向后兼容

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

### 6.5 单 Agent Runtime（实际采用方案）

经过设计评审，实际落地采用单 Agent Runtime 架构代替以上三种模式。详细设计见 `2026-07-23-phase2-partA-agent-core-design.md`。

#### 6.5.1 核心架构

| 组件 | 职责 | 输入 | 输出 |
|------|------|------|------|
| Guardrails | 安全护栏：拦截注入/越域/敏感信息 | 用户原始 query | allow / warn / block |
| Classifier | 意图/风险/复杂度三维分类 | query + 上下文 | {intent, risk, complexity} |
| Agent Runtime | ReAct 执行循环，按需加载能力 | query + 分类结果 | 最终回答 |
| Skills | 可复用操作流程（如"MySQL 故障排查流程"） | 参数 + 上下文 | 结构化结果 |
| RAG | 知识检索 | query + 过滤条件 | 文档 chunks |
| Tools | 细粒度执行单元（如 mysql_query） | 参数 | 执行结果 |
| PlanExecute | 复杂任务拆步执行管线 | Plan | 各步执行结果 |
| MCP Tools | 外部系统集成（MCP 协议） | 参数 | 执行结果 |
| HITL Gateway | 写操作人工审批 | 工具名 + 参数 | approve / reject |

#### 6.5.2 执行路径

不再固定走某一种 Agent 模式，而是由 Classifier 判断后选择路径：

| 场景 | Classifier 输出 | 执行路径 | LLM 调用次数 |
|------|----------------|---------|-------------|
| "什么是主从复制？" | {qa, low, simple} | 直接 LLM 回答 | 1 |
| "MySQL 延迟怎么排查" | {knowledge, low, simple} | RAG 检索 → LLM 回答 | 2 |
| "查 Jenkins #123 日志" | {tool, low, simple} | 加载工具 → ReAct 循环 → 回答 | N |
| "排查并修复 MySQL 延迟" | {complex, high, multi_step} | 加载能力 → PlanExecute → HITL → 回答 | N+M |

#### 6.5.3 与 6.1-6.4 的关系

原方案的三种 Agent 模式不删除，作为设计历程保留。它们在单 Agent 架构中的映射关系：

| 原模式 | 映射到单 Agent 架构 |
|--------|-------------------|
| Router + Specialist | Runtime 按需加载匹配的 Tools/RAG/Skills |
| Plan-and-Execute | Runtime 的内置 PlanExecute 管线（按需启用） |
| Supervisor + Multi-Agent | 归入 Phase 3 工作流编排 |

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
