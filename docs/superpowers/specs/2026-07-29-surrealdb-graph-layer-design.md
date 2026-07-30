# SurrealDB 图谱层设计

设计日期: 2026-07-29
状态: 初稿
关联阶段: Phase 3（工具生态与生产化）

> 在 Phase 3 中引入 SurrealDB 作为 Agent 运行时关系图谱的专用存储，
> 与 MySQL（记录型）+ Qdrant（向量型）并存，形成三层存储架构。

---

## 1. 概述

### 1.1 目标

解决 Agent 运行时关系数据的追溯问题。当前架构中：

- **MySQL** 存事件记录（`agent_trace_events`），适合按 session_id 线性查，不适合图遍历
- **Qdrant** 存向量，与关系数据无关

引入 SurrealDB 后，覆盖三类关系数据：

| 数据类型 | 说明 | 示例 |
|---------|------|------|
| Agent 调用链 | 请求经过 Supervisor→Router→Specialist→... 的完整路径 | 一次故障排查请求的完整路由 |
| ReAct 步骤图 | 同一步骤内 LLM 思考→工具调用→结果处理的循环链 | "查磁盘"请求的 3 轮 ReAct |
| Agent 拓扑 | Supervisor→Worker 的层级关系和路由规则 | 系统当前有哪些 Agent，谁是谁的下级 |

### 1.2 设计原则

1. **不替代现有存储**：MySQL、Qdrant、Redis 的职责不变
2. **最终一致性**：写入失败不影响核心 Agent 执行（可降级）
3. **数据有生命周期**：三层存储按时间分层，成本和查询能力匹配
4. **抽象接口**：GraphRecorder 封装写入细节，Agent Runtime 通过接口调用

---

## 2. 三层数据生命周期

### 2.1 总览

```
                             时间轴
  ────────────────────────────────────────────────────────────►
  │                           │                           │
  0d                          7d                          30d
  │                           │                           │
  ├────── 热层 ──────┼────── 温层 ──────┼────── 冷层 ────┤
  │  SurrealDB       │  SurrealDB       │  MySQL          │
  │  原始粒度         │  聚合粒度         │  事件归档        │
  │  完整图遍历       │  结构化概要       │  线性查询        │
```

### 2.2 各层详解

| 层级 | 存储 | 保留时间 | 数据形态 | 查询能力 | 典型场景 |
|------|------|---------|---------|---------|---------|
| **热** | SurrealDB | 0-7 天 | 原始记录：Step 全字段（含 input/output/token_count），Agent 拓扑，Session 元数据 | 完整图遍历，可回溯每一步的输入输出和耗时 | 在线排查："这个请求为什么慢？" |
| **温** | SurrealDB | 7-30 天 | 压缩记录：Step 保留结构字段（step_type/phase/status/duration_ms），**清空** input/output/token_count，按小时聚合摘要表 | 图结构可查，无 payload 明细 | 趋势分析："本周哪个 Agent 成功率最低？" |
| **冷** | MySQL | 30天-1年 | 平铺事件记录（现有 `agent_trace_events` 表），无图关系 | 按 session_id / 时间范围线性查 | 合规归档："审计查三个月前的某次操作" |

### 2.3 数据流

```
                    Agent Runtime
                         │
           ┌─────────────┼─────────────┐
           │             │             │
     ┌─────▼─────┐ ┌─────▼─────┐ ┌─────▼─────┐
     │ SurrealDB │ │ SurrealDB │ │  MySQL    │
     │   热层     │ │   温层     │ │   冷层     │
     │ (实时写入)  │ │ (定时聚合)  │ │ (实时写入)  │
     └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
           │             │             │
           │   7天后压缩  │   30天后    │
           └────────────►└────────────►│
                压缩调度       归档调度
```

### 2.4 数据量估算

以日请求 10,000（日均 ~150,000 个 Step）为例：

| 层级 | 时间范围 | 单 Step 大小 | Step 量 | 存储量 |
|------|---------|-------------|--------|-------|
| 热层 | 0-7 天 | ~200 字节 | ~105 万 | ~210 MB |
| 温层 | 7-30 天 | ~70 字节（压缩后） | ~345 万 | ~240 MB |
| **SurrealDB 合计** | 30 天 | — | ~450 万 | **~450 MB** |

加上边关系和 Agent 拓扑，总量约 **600 MB**，按 10 倍余量估算也才 **6 GB** —— 数据库完全无压力。

---

## 3. SurrealDB 数据模型

### 3.1 节点表

```surql
-- ========== Session 节点 ==========
DEFINE TABLE session TYPE ANY SCHEMAFULL;
DEFINE FIELD session_id ON session TYPE string;
DEFINE FIELD request_id ON session TYPE string;
DEFINE FIELD user_id ON session TYPE option<string>;
DEFINE FIELD status ON session TYPE string;       -- running / completed / failed
DEFINE FIELD started_at ON session TYPE datetime;
DEFINE FIELD ended_at ON session TYPE option<datetime>;
DEFINE INDEX idx_session_id ON session FIELDS session_id UNIQUE;

-- ========== Agent 节点 ==========
DEFINE TABLE agent TYPE ANY SCHEMAFULL;
DEFINE FIELD agent_id ON agent TYPE string;
DEFINE FIELD agent_type ON agent TYPE string;      -- supervisor / router / classifier / specialist / tool
DEFINE FIELD agent_name ON agent TYPE string;
DEFINE FIELD version ON agent TYPE option<string>;
DEFINE FIELD status ON agent TYPE string;           -- active / inactive
DEFINE INDEX idx_agent_id ON agent FIELDS agent_id UNIQUE;

-- ========== Step 节点（热层核心） ==========
DEFINE TABLE step TYPE ANY SCHEMAFULL;
DEFINE FIELD step_id ON step TYPE string;
DEFINE FIELD session_id ON step TYPE string;
DEFINE FIELD step_index ON step TYPE int;
DEFINE FIELD step_type ON step TYPE string;         -- llm_call / tool_call / human_approval / classify / route
DEFINE FIELD phase ON step TYPE string;              -- supervisor / classify / rag / tool / plan
DEFINE FIELD status ON step TYPE string;             -- running / success / error / timeout / blocked
DEFINE FIELD input ON step TYPE option<string>;      -- 热层保留
DEFINE FIELD output ON step TYPE option<string>;     -- 热层保留
DEFINE FIELD token_count ON step TYPE option<int>;   -- 热层保留
DEFINE FIELD duration_ms ON step TYPE int;
DEFINE FIELD agent_id ON step TYPE option<string>;   -- 执行此 step 的 agent
DEFINE FIELD parent_step_id ON step TYPE option<string>;  -- 前一步（构建调用链）
DEFINE FIELD created_at ON step TYPE datetime DEFAULT time::now();
DEFINE INDEX idx_step_id ON step FIELDS step_id UNIQUE;
DEFINE INDEX idx_step_session ON step FIELDS session_id;
```

### 3.2 边表（关系）

```surql
-- belongs_to: Step → Session
DEFINE TABLE belongs_to TYPE ANY SCHEMAFULL;
DEFINE FIELD in ON belongs_to TYPE record<step>;
DEFINE FIELD out ON belongs_to TYPE record<session>;

-- executes: Agent → Step
DEFINE TABLE executes TYPE ANY SCHEMAFULL;
DEFINE FIELD in ON executes TYPE record<agent>;
DEFINE FIELD out ON executes TYPE record<step>;

-- routes_to: Agent → Agent（拓扑关系，Supervisor → Worker）
DEFINE TABLE routes_to TYPE ANY SCHEMAFULL;
DEFINE FIELD in ON routes_to TYPE record<agent>;
DEFINE FIELD out ON routes_to TYPE record<agent>;
```

### 3.3 温层：聚合摘要表

```surql
-- ========== 按小时聚合（温层） ==========
DEFINE TABLE step_hourly TYPE ANY SCHEMAFULL;
DEFINE FIELD hour_bucket ON step_hourly TYPE datetime;       -- 聚合到整点
DEFINE FIELD agent_id ON step_hourly TYPE string;
DEFINE FIELD step_type ON step_hourly TYPE string;
DEFINE FIELD phase ON step_hourly TYPE string;
DEFINE FIELD total_count ON step_hourly TYPE int;
DEFINE FIELD success_count ON step_hourly TYPE int;
DEFINE FIELD error_count ON step_hourly TYPE int;
DEFINE FIELD avg_duration_ms ON step_hourly TYPE float;
DEFINE FIELD p95_duration_ms ON step_hourly TYPE float;
DEFINE FIELD total_tokens ON step_hourly TYPE option<int>;
DEFINE INDEX idx_hourly_bucket ON step_hourly FIELDS hour_bucket, agent_id;

-- ========== 按天聚合 ==========
DEFINE TABLE step_daily TYPE ANY SCHEMAFULL;
DEFINE FIELD day_bucket ON step_daily TYPE string;           -- "2026-07-29"
DEFINE FIELD agent_id ON step_daily TYPE string;
DEFINE FIELD total_count ON step_daily TYPE int;
DEFINE FIELD success_rate ON step_daily TYPE float;
DEFINE FIELD avg_duration_ms ON step_daily TYPE float;
DEFINE FIELD p95_duration_ms ON step_daily TYPE float;
DEFINE INDEX idx_daily_bucket ON step_daily FIELDS day_bucket, agent_id;
```

### 3.4 实体关系图

```
                    ┌──────────┐
                    │  Session │
                    └────┬─────┘
                         │ belongs_to (1:N)
                    ┌────▼─────┐
                    │   Step   │ ◄──── parent_step_id ──── 自引用调用链
                    └────┬─────┘
                         │ executes (N:1)
                    ┌────▼─────┐
                    │   Agent  │
                    └────┬─────┘
                         │ routes_to (1:N, Supervisor→Worker)
                    ┌────▼─────┐
                    │   Agent  │
                    └──────────┘

              步明细表(热层)                          聚合表(温层)
              step(st满字段)  ──7天后压缩──►  step(fields 清空 input/output/tokens)
                                                │
                                                └── 同时归入 step_hourly(聚合写入)
```

---

## 4. 数据流设计

### 4.1 写入流程

```
Agent Runtime 执行到关键节点时，调用 GraphRecorder：

【Session 开始】
  GraphRecorder.record_session_start(
      session_id="sess_001",
      request_id="req_abc",
      user_id="u_123"
  )

【每个 Step 完成】
  GraphRecorder.record_step(
      step_id="st_3",
      session_id="sess_001",
      step_index=3,
      step_type="llm_call",
      phase="rag",
      status="success",
      input='{"query": "磁盘使用率"}',
      output='{"result": "85%"}',
      token_count=452,
      duration_ms=2800,
      agent_id="rag_agent_01",
      parent_step_id="st_2"        -- 上一步的 step_id
  )

【Session 结束】
  GraphRecorder.record_session_end(
      session_id="sess_001",
      status="completed"
  )
```

### 4.2 写入策略：最终一致性

```
请求到达 → 异步写入 SurrealDB（不阻塞 Agent 主流程）
                │
           ┌────┴────┐
           │         │
       写入成功    写入失败
           │         │
           │    ┌────┴────┐
           │    │          │
           │  重试 3 次  放弃（记日志，不阻塞）
           │    │
           │   └─ 仍失败 → 打印 WARN 日志，继续
           │
        正常返回
```

关键原则：**SurrealDB 写入失败不影响 Agent 主流程**。Agent Runtime 只保证 MySQL 的 `agent_trace_events` 写入，SurrealDB 是增强层。

### 4.3 热 → 温 压缩流程

定时任务（每 6 小时执行一次），处理 7-7.25 天前过期的数据：

```python
async def compress_to_warm():
    """将 7 天前的热数据压缩为温数据"""
    cutoff = datetime.utcnow() - timedelta(days=7)
    next_cutoff = cutoff + timedelta(hours=6)  # 每次处理 6 小时窗口

    # 1. 生成按小时聚合数据
    raw_steps = await db.query("""
        SELECT
            time::floor(created_at, 1h) AS hour_bucket,
            agent_id, step_type, phase,
            count() AS total_count,
            count_if(status == 'success') AS success_count,
            count_if(status IN ['error', 'timeout']) AS error_count,
            math::mean(duration_ms) AS avg_duration,
            percentile(duration_ms, 0.95) AS p95_duration,
            sum(token_count) AS total_tokens
        FROM step
        WHERE created_at >= $cutoff AND created_at < $next_cutoff
        GROUP BY hour_bucket, agent_id, step_type, phase
    """, cutoff, next_cutoff)

    for row in raw_steps:
        await db.create("step_hourly", row)

    # 2. 清空明细 step 的 payload 字段
    await db.query("""
        UPDATE step SET input = NONE, output = NONE, token_count = NONE
        WHERE created_at >= $cutoff AND created_at < $next_cutoff
    """, cutoff, next_cutoff)
```

### 4.4 温 → 冷 归档流程（可选）

MySQL 已有 `agent_trace_events` 表作为冷层，这里只做 SurrealDB 数据清理（非必要——30 天的压缩数据只有 ~450 MB，存着完全没负担）。

默认策略：**温层数据保留在 SurrealDB，不主动删除**。如需清理：

```surql
DELETE step WHERE created_at < time::now() - 30d;
DELETE step_hourly WHERE hour_bucket < time::now() - 30d;
DELETE step_daily WHERE day_bucket < time::now() - 90d;
```

通过配置控制：

```bash
SA_SURREALDB_WARM_RETENTION_DAYS=30      # 温层保留天数
SA_SURREALDB_TTL_DAYS=0                    # 0 = 不自动删除（默认）
```

---

## 5. 典型查询

### 5.1 热层查询（原始粒度，0-7 天）

```surql
-- 查请求的完整调用链（按步骤顺序）
SELECT * FROM step
WHERE session_id = "sess_001"
ORDER BY step_index ASC;

-- 查某一步的入参和出参
SELECT step_type, phase, input, output, token_count
FROM step WHERE step_id = "st_3";

-- 查调用链关系（通过 parent_step_id 自引用）
SELECT * FROM step
WHERE session_id = "sess_001"
TRAVERSE parent_step_id;

-- 查失败步骤及上下文
SELECT step_index, step_type, phase, status, output
FROM step WHERE status = "error"
AND created_at > time::now() - 1d;
```

### 5.2 温层查询（聚合粒度，7-30 天）

```surql
-- 查某 Agent 过去一周的成功率趋势（按小时）
SELECT hour_bucket, total_count, success_count, 
       success_count / total_count AS success_rate
FROM step_hourly
WHERE agent_id = "rag_agent_01"
AND hour_bucket > time::now() - 14d
ORDER BY hour_bucket ASC;

-- 查全局慢步骤分布
SELECT phase, step_type, 
       math::mean(avg_duration_ms) AS avg_dur,
       math::max(p95_duration_ms) AS max_p95
FROM step_hourly
WHERE hour_bucket > time::now() - 7d
GROUP BY phase, step_type;

-- 查按日的系统整体健康度
SELECT day_bucket, 
       sum(total_count) AS total,
       sum(success_count) / sum(total_count) AS success_rate
FROM step_daily
WHERE day_bucket > time::now() - 28d
GROUP BY day_bucket;
```

### 5.3 图遍历查询

```surql
-- 查某个 Agent 拓扑下的所有子 Agent
SELECT agent_id, agent_name, agent_type
FROM agent WHERE agent_id IN (
    SELECT VALUE out FROM routes_to WHERE in.agent_id = "supervisor_01"
);

-- 查某个请求涉及的所有 Agent
SELECT DISTINCT agent_id, agent_name, agent_type
FROM agent WHERE agent_id IN (
    SELECT VALUE agent_id FROM step 
    WHERE session_id = "sess_001"
);
```

---

## 6. 代码设计

### 6.1 目录结构

```
src/super_agent/graph/
├── __init__.py
├── client.py           # SurrealDB 客户端封装
├── models.py           # 数据模型 Pydantic 定义
├── recorder.py         # GraphRecorder：运行时埋点
├── compressor.py       # 热→温 压缩调度任务
├── queries.py          # 预定义查询方法
└── config.py           # SurrealDB 配置
```

### 6.2 核心接口

```python
# graph/recorder.py

class GraphRecorder:
    """
    Agent Runtime 关系数据写入 SurrealDB。
    在 Runtime 关键节点调用，与 MySQL agent_trace_events 并存。
    写入失败不阻塞主流程（WARN 日志后继续）。
    """

    def __init__(self, client: SurrealDBClient):
        self._client = client

    async def record_session_start(
        self, session_id: str, request_id: str,
        user_id: str | None = None
    ):
        ...

    async def record_session_end(
        self, session_id: str, status: str
    ):
        ...

    async def record_step(self, step: AgentStep):
        """记录 ReAct 中的每一步"""
        ...

    async def record_agent_topology(
        self, parent_agent_id: str, child_agent_id: str
    ):
        """记录 Supervisor→Worker 的关系"""
        ...
```

### 6.3 配置

```python
class SurrealDBConfig:
    """
    前缀: SA_SURREALDB_
    """
    enabled: bool = False
    host: str = "localhost"
    port: int = 8000
    user: str = "root"
    password: str = "root"
    namespace: str = "super_agent"
    database: str = "agent_graph"

    # 生命周期
    warm_retention_days: int = 30       # 温层数据保留天数
    compress_interval_minutes: int = 360  # 压缩任务执行间隔（6 小时）
```

---

## 7. 与 Phase 3 其他模块的关系

| 模块 | 关系 |
|------|------|
| **OpenTelemetry** | 不变。OTel 仍消费 MySQL `agent_trace_events`，不依赖 SurrealDB |
| **工作流编排** | Workflow Engine 的 Step 执行记录同步写入 SurrealDB（共享 step 模型） |
| **MCP Server** | MCP Server 可通过 SurrealDB 查询 Agent 拓扑和调用链（新增 API） |
| **自定义工具** | 工具执行记录作为 `step_type=tool_call` 写入 SurrealDB |
| **Skills 系统** | Skills 注册时写入 SurrealDB agent 表，形成 Skills→Agent 映射 |

---

## 8. 与现有存储的职责划分

```
            MySQL                        SurrealDB                   Qdrant
    ┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
    │ 记录型持久化         │    │ 关系图谱             │    │ 向量检索             │
    │                     │    │                     │    │                     │
    │ • sessions          │    │ • 调用链（图遍历）    │    │ • Chunk embeddings  │
    │ • messages          │    │ • Agent 拓扑         │    │ • 向量相似度搜索     │
    │ • users             │    │ • ReAct 步骤图       │    │ • metadata 过滤      │
    │ • audit_log         │    │ • Skills 映射        │    │                     │
    │ • agent_trace_events│    │ • 小时/天聚合        │    │                     │
    │                     │    │                     │    │                     │
    │ 查询方式: SQL JOIN   │    │ 查询方式: 图遍历     │    │ 查询方式: 向量距离    │
    │ 特点: 强一致，永久   │    │ 特点: 最终一致，有 TTL│    │ 特点: ANN 近似检索    │
    └─────────────────────┘    └─────────────────────┘    └─────────────────────┘
```

---

## 9. 配置汇总

```bash
# ── SurrealDB ──
SA_SURREALDB_ENABLED=false
SA_SURREALDB_HOST=localhost
SA_SURREALDB_PORT=8000
SA_SURREALDB_USER=root
SA_SURREALDB_PASSWORD=root
SA_SURREALDB_NAMESPACE=super_agent
SA_SURREALDB_DATABASE=agent_graph
SA_SURREALDB_WARM_RETENTION_DAYS=30
SA_SURREALDB_COMPRESS_INTERVAL=360      # 分钟
```

---

## 10. 发布顺序

```
Phase 3 新增 SurrealDB 模块（与 MCP Server / 自定义工具 / OTel / 工作流并列）：

Part A（基础接入）：
  - SurrealDB 客户端封装 + 配置
  - GraphRecorder 核心写入接口
  - Agent Runtime 关键节点埋点（Session 开始/结束、Step 完成）

Part B（压缩与聚合）：
  - 热→温压缩任务
  - 聚合表生成
  - 查询接口（MCP Server 可调用）

Part C（查询与可视化）：
  - 追溯 API 暴露
  - Grafana Dashboard 展示趋势
```
