# Phase 3: 工具生态与生产化

设计日期: 2026-07-27
状态: 初稿

> 基于需求文档 3.3 节整理，AgentSkill 加载器已移到 Phase 2 Part A（Skills 系统），本阶段聚焦剩余 4 个交付项。

---

## 1. 概述

### 1.1 目标

完善工具集成和生产级能力，让 Super Agent 具备对外提供能力、生产可观测和复杂工作流编排的能力。

### 1.2 与 Phase 2 的关系

```
Phase 2 Part A                    Phase 3
──────────────────────────────────────────────
Agent Runtime（单 Agent 执行）     Workflow 编排（多 Agent/多步骤编排）
Skills 系统（消费 SKILL.md）       MCP Server（对外提供能力）
ToolRegistry（统一注册表）         自定义工具框架（热插拔外部工具）
HITL（写操作审批）                 OpenTelemetry（生产级追踪）
Docker 沙箱（隔离执行不可信脚本）
```

### 1.3 交付清单

| 编号 | 模块 | 优先级 | 依赖 |
|------|------|--------|------|
| P3-1 | MCP Server（Python + Java 双实现） | P1 | Phase 2 的 MCP Client / ToolRegistry |
| P3-2 | 自定义工具框架 | P1 | Phase 2 的 ToolRegistry |
| P3-3 | OpenTelemetry + Jaeger | P1 | 无 |
| P3-4 | 工作流编排 | P2 | Phase 2 的 PlanExecute |

---

## 2. MCP Server（Python + Java 双实现）

### 2.1 定义

Phase 2 已经设计了 **MCP Client 侧**（连接外部 MCP Server，消费其 Tools），
Phase 3 需要补充 **MCP Server 侧**：将 Super Agent 的内部能力（Tools / RAG / Skills）通过 MCP 协议暴露给外部 Agent 使用。

**要求双语言实现**：Python 版内嵌在 Super Agent 进程中，Java 版作为独立服务部署，两者能力等价。

### 2.2 架构

```
外部 Agent（Claude Code / Cursor / 其他 MCP Client）
         │
         │ MCP 协议（JSON-RPC over stdio/SSE）
         ├──────────────────────┐
         ▼                      ▼
   Python MCP Server      Java MCP Server
   （内嵌 Super Agent）   （独立部署）
         │                      │
         ├── ToolRegistry 代理   ├── 同能力集
         ├── RAG 查询代理        ├── 适合 Java 技术栈团队集成
         └── Skills 查询代理     └── 可独立升级/扩容
```

### 2.3 暴露的能力

| MCP Tool 名称 | 映射到内部 | 说明 |
|--------------|-----------|------|
| `tool_list` | ToolRegistry.list_all() | 列出所有可用工具 |
| `tool_execute` | ToolRegistry.execute() | 调用指定工具 |
| `rag_search` | Retriever.retrieve() | 知识库检索 |
| `skill_list` | SkillManager | 列出已注册的技能 |

### 2.4 Python 实现

用社区成熟的 MCP SDK（如 `mcp` Python 包），避免手搓 JSON-RPC：

```python
from mcp.server import Server, stdio_server

class SuperAgentMCPServer:
    def __init__(self, tool_registry, rag_manager, skill_manager):
        self.server = Server("super-agent")
        self.server.list_tools()(self._list_tools)
        self.server.call_tool()(self._call_tool)

    async def serve_stdio(self):
        """stdio 模式：适用于 Claude Code 等本地 Agent"""
        async with stdio_server() as (read, write):
            await self.server.run(read, write)

    async def serve_sse(self, host: str, port: int):
        """SSE 模式：适用于远程 Agent"""
        # 通过 SSE 对外暴露
```

### 2.5 Java 实现

Java 版作为**独立服务**部署，通过 HTTP gRPC 与 Super Agent 后端通信以获取 Tool/RAG/Skills 数据，MCP 协议层用 Java MCP SDK 实现：

```java
// 使用 Java MCP SDK（如 modelcontextprotocol/java-sdk）
@SpringBootApplication
public class SuperAgentMcpServer {

    @Bean
    public McpServer mcpServer(ToolRegistryClient toolClient) {
        return McpServer.using(
            new StdioServerTransport(),
            new McpServerSpec("super-agent-java")
        ).tools(toolClient.listTools())
         .build();
    }
}
```

Java 版适用场景：
- 技术栈以 Java 为主的团队，直接复用 Spring Boot 生态
- 需要独立扩容 MCP Server 实例
- 集成公司已有的 Java 监控/日志体系

### 2.6 双实现策略

| 对比 | Python 版 | Java 版 |
|------|----------|---------|
| 部署方式 | 内嵌 Super Agent 进程 | 独立 Spring Boot 服务 |
| 协议栈 | stdio + SSE | SSE 为主 |
| 能力集 | 全量（直接调内部 API） | 全量（通过 HTTP/gRPC 调后端） |
| 启动成本 | 零（随 Agent 启动） | 需要单独部署 |
| 适用场景 | 本地开发、轻量集成 | 生产环境、Java 技术栈集成 |
| 维护方 | Super Agent 团队 | Java 基础设施团队 |

两个实现共享相同的 MCP 协议规范，外部 Agent 无差别连接。

### 2.7 配置

```bash
# Python 版（内嵌）
SA_MCP_SERVER_ENABLED=true           # MCP Server 总开关
SA_MCP_SERVER_TRANSPORT=stdio        # stdio / sse
SA_MCP_SERVER_HOST=0.0.0.0           # SSE 模式下监听地址
SA_MCP_SERVER_PORT=8100              # SSE 模式下端口
SA_MCP_SERVER_EXPOSE=all             # all / tools-only / rag-only / skills-only

# Java 版（独立部署）
# 由 Java 服务的 application.yml 管理
# super-agent.backend-url=http://super-agent:8000
# super-agent.mcp.port=8101
```


## 3. 自定义工具框架

### 3.1 定义

让开发者无需修改 Super Agent 核心代码，就能通过**外部文件或部署**新增工具。

### 3.2 两种方式

**方式 A：声明式（轻量）**

在配置路径下放一个工具定义文件，运行时自动注册：

```yaml
# data/custom-tools/my-api-tool.yaml
name: weather_query
description: 查询指定城市的天气
parameters:
  type: object
  properties:
    city:
      type: string
      description: 城市名称
  required: [city]
endpoint: https://api.weather.com/query
method: GET
auth:
  type: header
  key: X-API-Key
  value_from_env: WEATHER_API_KEY
```

Runtime 解析后包装成 Tool 注册到 ToolRegistry，执行时发 HTTP 请求。

**方式 B：脚本式（灵活）**

把脚本放到约定目录下，参数通过环境变量或 stdin/stdout 传递：

```
data/custom-tools/
├── tools.yaml              # 工具注册表
├── deploy-server.py        # 部署工具（Python）
├── check-disk.sh           # 磁盘检查（Bash）
└── send-notification.js    # 通知发送（Node.js）
```

```yaml
# tools.yaml
tools:
  - name: deploy_server
    description: 部署应用到指定服务器
    script: deploy-server.py    # 相对路径
    interpreter: python3
    parameters:
      type: object
      properties:
        host:
          type: string
        version:
          type: string
  - name: check_disk
    description: 检查磁盘使用率
    script: check-disk.sh
    interpreter: bash
```

### 3.3 与 ToolRegistry 的关系

```
自定义工具框架 → 扫描 data/custom-tools/
         ↓
    解析声明（YAML / 脚本）
         ↓
    生成 Tool 实例（BaseTool 接口兼容）
         ↓
    注册到 ToolRegistry（与内部 Tools 同级）
         ↓
    LLM 无差别调用
```

### 3.4 配置

```bash
SA_CUSTOM_TOOLS_ENABLED=true
SA_CUSTOM_TOOLS_PATH=./data/custom-tools
```

---

## 4. OpenTelemetry + Jaeger 生产链路追踪

### 4.1 定义

Phase 1 的开发期追踪用 LangSmith，Phase 3 补充生产级追踪。

**关键设计前提**：Part A 已经定义了 `agent_trace_events` 表和 `agent_sessions` 表，并在 Agent Runtime 的关键路径埋了点。Part C 不再重复埋点，而是**消费 Part A 的数据**。

```
Part A 产出（已埋点）               Part C 消费
────────────────────────────────────────────────────
agent_trace_events 表              读取后生成 OpenTelemetry Span
agent_sessions 表                  作为 Span 的附属信息
AgentState.messages                用于重建调用链

Part A = 数据生产层（只写不读）
Part C = 数据消费层（只读不生产）
```

### 4.2 架构

```
Agent Runtime（Part A）
    │
    ├─ Guardrails  → record() → agent_trace_events
    ├─ Classifier  → record() → agent_trace_events
    ├─ ReAct 循环  → record() → agent_trace_events  ← 每步 LLM 调用、工具调用
    └─ 会话结束    → archive  → agent_sessions       ← 完整归档
    │
    ▼
agent_trace_events 表 + agent_sessions 表
    │
    ▼
OpenTelemetry Collector（Part C）
    ├─ 读取 agent_trace_events 生成 Span
    │   └─ 每条 record → OTel Span（parent_event_id → parent-child 关系）
    ├─ 读取 agent_sessions 补充 Session 元数据
    └─ 导出到 Jaeger → 可视化 Trace
        导出到 Prometheus → 聚合指标
        导出到 Loki → 日志关联
```

### 4.3 埋点方案对比

```
方案一：代码内嵌 OTel SDK（传统方式，已否决）
  AgentRuntime.run():
      with tracer.start_as_current_span("runtime.run"):
          ...
  问题：Part A 需要引入 OTel SDK，Part C 改动时得改 Part A 代码

方案二：Part A 写表 + Part C 消费（选定方案）
  Part A:
      self.trace_recorder.record(event)  →  INSERT INTO agent_trace_events
      零 OTel 依赖，纯 SQL 写入

  Part C:
      OTel Collector 读取 agent_trace_events 表
      或者自定义 Exporter 将表数据转换为 OTel Span
```

### 4.4 Trace 重建逻辑

Part C 将 `agent_trace_events` 表的数据转换为 OTel Trace：

```python
# Part C 的转换逻辑（读取 Part A 写入的表 → 生成 OTel Span）
async def rebuild_trace(session_id: str):
    events = await db.query(
        "SELECT * FROM agent_trace_events WHERE session_id = ? ORDER BY id",
        session_id
    )
    for event in events:
        span_name = f"{event.phase}.{event.event_type}"
        span = tracer.start_span(
            name=span_name,
            context=trace_api.SpanContext(
                trace_id=hash_to_128bit(session_id),   # session_id 映射为 trace_id
                span_id=generate_span_id(event.id),
                is_remote=False,
            ),
            start_time=event.created_at,
        )
        span.set_attribute("phase", event.phase)
        span.set_attribute("step", event.step_index)
        span.set_attribute("duration_ms", event.duration_ms)
        span.set_attribute("status", event.status)

        if event.parent_event_id:
            span.set_parent(rebuild_parent_context(event.parent_event_id))

        if event.event_type == "llm_call":
            span.set_attribute("llm.tokens", event.output.get("token_count", 0))
        elif event.event_type == "tool_execute":
            span.set_attribute("tool.name", event.input.get("tool_name"))
            span.set_attribute("tool.status", event.status)

        span.end()
```

**trace_id 生成规则**：以 `session_id` 的 hash 作为 trace_id，保证同一会话的所有 Span 属于同一个 Trace。

### 4.5 查询能力

基于 Part A 写入的数据，Part C 可以提供：

```sql
-- 查看某个 session 的完整 trace 时间线
SELECT event_type, phase, duration_ms, status
FROM agent_trace_events
WHERE session_id = 'xxx'
ORDER BY id;

-- 查看慢步骤（耗时 > 5s）
SELECT session_id, event_type, duration_ms
FROM agent_trace_events
WHERE duration_ms > 5000
  AND created_at > NOW() - INTERVAL 1 DAY
ORDER BY duration_ms DESC;

-- 查看失败的步骤
SELECT session_id, phase, event_type, output
FROM agent_trace_events
WHERE status IN ('error', 'timeout', 'blocked')
  AND created_at > NOW() - INTERVAL 1 HOUR;
```

### 4.6 导出

```
agent_trace_events 表
    │
    ├── 方式 A: OTel Collector + JDBC Connector
    │    └── Collector 直接查 MySQL 表 → 转 OTel Span → Jaeger
    │
    ├── 方式 B: 自定义 Exporter（Python 脚本）
    │    └── 定时查询 MySQL → 转换为 OTel Span → 推送到 Collector
    │
    └── 方式 C: 直接查 MySQL（轻量方案）
         └── Grafana 直接查 agent_trace_events 表，不用 Jaeger
```

### 4.7 配置

```bash
# Part C 本模块配置
SA_OTEL_ENABLED=false                # 生产追踪总开关
SA_OTEL_SERVICE_NAME=super-agent     # 服务名
SA_OTEL_EXPORTER=jaeger              # jaeger / otlp / console
SA_OTEL_ENDPOINT=http://jaeger:4318  # Jaeger 地址
SA_OTEL_SAMPLE_RATE=0.5              # 采样率（生产建议 0.1-0.5）

# Part A 的 SA_STATE_TRACE_ENABLED=true 必须开启，否则本模块无数据可用
# Part C 不新增同级别埋点配置，所有 TraceEvent 相关配置在 Part A 的 SA_STATE_* 下管理
```

### 4.8 Part A / Part C 职责边界

```
Part A（写入层）
  ├─ agent_trace_events 表定义 + 写入
  ├─ agent_sessions 表定义 + 写入
  ├─ TraceEventRecorder 实现
  ├─ 埋点位置：Guardrails / Classifier / Runtime / HITL / PlanExecute
  └─ 不依赖任何 OTel SDK

Part C（消费层）
  ├─ 读取 agent_trace_events → 生成 OTel Span
  ├─ 读取 agent_sessions → 补充元数据
  ├─ 对接 Jaeger / Prometheus / Loki
  ├─ 提供 SQL 查询视图和 Grafana Dashboard
  └─ 不新增同级别埋点表

边界规则：
  1. Part C 永远不 INSERT INTO agent_trace_events，只 SELECT
  2. Part C 如果发现 agent_trace_events 缺少字段，在表中加字段而非新建表
  3. Part C 引入 OTel SDK 时无需改 Part A 的代码
```

---

## 5. 工作流编排

### 5.1 定义

工作流编排是 Phase 2 单 Agent Runtime 的**扩展**。当任务需要跨多个 Agent 或跨多步骤人工审批时，使用工作流来描述和执行。

### 5.2 与 PlanExecute 的区别

```
PlanExecute（Phase 2）          ↔     Workflow（Phase 3）
单 Agent 内多步骤                     多 Agent / 多系统协作
LLM 动态规划步骤                       预定义流程模板
无状态，每次重新规划                    有状态，持久化执行进度
适合临时排查任务                       适合固化 SOP
示例："排查 MySQL 延迟"                示例："服务器采购审批流程"
```

### 5.3 工作流定义

YAML 定义，版本化管理：

```yaml
# data/workflows/server-provision.yaml
name: server-provision
version: 1.0.0
description: 服务器采购审批 → 部署 → 录入 CMDB

steps:
  - id: request
    name: 提交申请
    type: form                    # 人工填表
    schema:
      type: object
      properties:
        cpu: {type: integer}
        memory: {type: integer}
        disk: {type: integer}

  - id: approval
    name: 主管审批
    type: human_approval          # 人工审批
    depends_on: [request]
    timeout: 86400                # 24 小时

  - id: deploy
    name: 自动部署
    type: tool_call               # 调工具
    tool: server_provision
    depends_on: [approval]
    condition: "approval.result == approved"

  - id: cmdb
    name: 录入 CMDB
    type: tool_call
    tool: cmdb_upsert
    depends_on: [deploy]
```

### 5.4 Runtime 集成

```
工作流不替代 Agent Runtime，而是作为 Runtime 的一个可选执行模式：

用户请求 → Guardrails → Classifier
         ↓
Classifier 判定：
  ┌── 单步骤 / 排查类 → Agent Runtime（ReAct / PlanExecute）
  │
  └── 符合预定义流程  → Workflow Engine
                          ├── 加载对应 YAML
                          ├── 解析 DAG 依赖
                          ├── 按序执行每个 step
                          │   ├── form → 返回表单给用户填写
                          │   ├── human_approval → HITL
                          │   └── tool_call → ToolRegistry
                          ├── 记录执行状态到 MySQL
                          └── 完成 → 通知用户
```

### 5.5 工作流引擎核心职责

| 职责 | 说明 |
|------|------|
| DAG 调度 | 解析步骤依赖，并行执行无依赖的步骤 |
| 状态持久化 | 执行进度写到 MySQL，支持中断恢复 |
| 超时管理 | 每个 step 独立超时，超时则流程终止 |
| 条件分支 | if/else 跳过或选择路径 |
| 人工节点 | 表单填写、审批、确认 |
| 版本控制 | 流程定义版本化，运行时指定版本 |

### 5.6 配置

```bash
SA_WORKFLOW_ENABLED=false
SA_WORKFLOW_PATH=./data/workflows
SA_WORKFLOW_MAX_EXECUTION_TIME=604800  # 最大执行时间（7 天）
```

---

## 6. 组件间关系总结

```
                              Agent
                               |
          ┌────────────────────┼────────────────────┐
          │                    │                    │
     Skills 系统           Agent Runtime       Workflow Engine
   (Phase 2 Part A)      (Phase 2 Part A)     (Phase 3)
          │                    │                    │
          │              ┌─────┴─────┐              │
          │              │           │              │
     Docker 沙箱    ToolRegistry     │              │
   (Phase 2 Part A)  (Phase 2)      │              │
          │              │          │              │
          │        ┌─────┴─────┐    │              │
          │        │           │    │              │
        MCP Server  内部 Tools   MCP Client
        (Phase 3)  (Phase 1)   (Phase 2)
                               │
                    ┌──────────┴──────────┐
                    │                     │
           agent_trace_events       agent_sessions
           (Part A 写入)            (Part A 写入)
                    │
                    ▼
           OpenTelemetry (Part C)
           消费 Part A 数据，不做二次埋点

可观测性：LangSmith（Phase 1 开发期） + Part A 埋点 + Part C 消费
```

## 7. 配置汇总

```python
class Phase3Config:
    """
    前缀: SA_PHASE3_
    """
    # MCP Server
    mcp_server_enabled: bool = True
    mcp_server_transport: str = "stdio"        # stdio / sse
    mcp_server_host: str = "0.0.0.0"
    mcp_server_port: int = 8100
    mcp_server_expose: str = "all"             # all / tools-only

    # 自定义工具
    custom_tools_enabled: bool = True
    custom_tools_path: str = "./data/custom-tools"

    # OpenTelemetry
    otel_enabled: bool = False
    otel_service_name: str = "super-agent"
    otel_exporter: str = "jaeger"              # jaeger / otlp / console
    otel_endpoint: str = "http://jaeger:4318"

    # 工作流
    workflow_enabled: bool = False
    workflow_path: str = "./data/workflows"
```

## 8. 测试策略

| 模块 | 测试重点 |
|------|---------|
| MCP Server | 协议一致性、工具调用正确性、错误传递 |

| 自定义工具 | YAML 解析、参数校验、失败回退 |
| OpenTelemetry | Span 父子关系、采样、导出 |
| 工作流 | DAG 调度、状态持久化、条件分支、中断恢复 |

## 9. 发布顺序建议

```
Phase 3 Part A（先行）：
  - MCP Server（Python + Java）  ← 对外提供能力，优先级取决于是否需要与其他 Agent 互通

Phase 3 Part B：
  - 自定义工具框架               ← 热插拔工具，降低新增工具门槛
  - OpenTelemetry + Jaeger       ← 生产级追踪，与自定义工具一并上线

Phase 3 Part C：
  - 工作流编排              ← 最复杂，依赖 PlanExecute 成熟后再做
```
