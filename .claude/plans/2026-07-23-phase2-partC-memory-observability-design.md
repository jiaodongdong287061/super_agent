# Phase 2 - Part C: 记忆系统与 Agent 可观测性设计

设计日期: 2026-07-23
状态: 设计稿

## 1. 概述

记忆系统和 Agent 可观测性是 Phase 2 的数据基础设施层。记忆系统让 Agent 具备跨会话的学习能力，可观测性让 Agent 的每个决策可追溯、可审计。

---

## 2. 记忆系统

### 2.1 模块划分

```
src/super_agent/memory/
├── __init__.py
├── base.py                   # BaseMemory 抽象接口
├── short_term.py             # RedisShortTermMemory
├── long_term.py              # MySQLLongTermMemory
├── manager.py                # MemoryManager（统一入口）
└── models.py                 # MemoryItem / MemoryQuery
```

### 2.2 分层架构

```
           MemoryManager
           ↙           ↘
    ShortTermMemory   LongTermMemory
    (Redis)           (MySQL)
    ────────────     ────────────
    TTL 过期         持久化存储
    会话上下文         用户偏好
    Agent 中间状态     经验知识
    最近 K 轮对话      历史问答记录
```

### 2.3 BaseMemory 接口

```python
class BaseMemory(ABC):
    @abstractmethod
    async def save(self, key: str, value: Any, ttl: int | None = None) -> None: ...
    @abstractmethod
    async def load(self, key: str) -> Any | None: ...
    @abstractmethod
    async def delete(self, key: str) -> None: ...
    @abstractmethod
    async def exists(self, key: str) -> bool: ...
    @abstractmethod
    async def clear(self, session_id: str) -> None: ...
```

### 2.4 短期记忆（Redis）

#### Redis 数据结构设计

```
# 会话消息列表（List）
session:{session_id}:messages  →  LPUSH 消息 JSON，LTRIM 保留最新 N 条

# Agent 上下文状态（Hash）
session:{session_id}:state     →  HSET 各字段（current_mode, intermediate_steps 等）

# 会话元数据（Hash）
session:{session_id}:meta      →  HSET（user_id, created_at, status, ttl）

# 用户会话索引（Set）
user:{user_id}:sessions        →  SADD session_id

# TTL
session:{session_id}:*         →  EXPIRE {ttl}（默认 3600s）
```

```python
class RedisShortTermMemory(BaseMemory):
    def __init__(self, redis_url: str, ttl: int = 3600, key_prefix: str = "session:"):
        self.redis = redis.from_url(redis_url)
        self.ttl = ttl
        self.prefix = key_prefix

    async def save_message(self, session_id: str, msg: Message) -> None:
        key = f"{self.prefix}{session_id}:messages"
        await self.redis.lpush(key, msg.model_dump_json())
        await self.redis.ltrim(key, 0, 99)  # 保留最近 100 条
        await self.redis.expire(key, self.ttl)

    async def get_history(self, session_id: str, limit: int = 20) -> list[Message]:
        key = f"{self.prefix}{session_id}:messages"
        items = await self.redis.lrange(key, 0, limit - 1)
        return [Message(**json.loads(m)) for m in items]

    async def save_state(self, session_id: str, state: AgentState) -> None:
        key = f"{self.prefix}{session_id}:state"
        await self.redis.hset(key, mapping={
            "current_mode": state.current_mode.value,
            "messages_count": len(state.messages),
            "json": state.model_dump_json(),
        })
        await self.redis.expire(key, self.ttl)
```

### 2.5 长期记忆（MySQL）

#### 表结构

```sql
-- 会话记录
CREATE TABLE sessions (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',   -- active / paused / closed
    mode VARCHAR(32) NOT NULL DEFAULT 'router',
    config JSON,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    INDEX idx_user_id (user_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 消息记录
CREATE TABLE messages (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    role VARCHAR(16) NOT NULL,              -- user / assistant / system / tool
    content TEXT NOT NULL,
    metadata JSON,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_session_id (session_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 用户偏好
CREATE TABLE user_preferences (
    user_id VARCHAR(64) PRIMARY KEY,
    preferred_model VARCHAR(64) DEFAULT '',
    preferred_mode VARCHAR(32) DEFAULT 'router',
    system_prompt TEXT,
    metadata JSON,
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 经验知识（Agent 跨会话学习产物）
CREATE TABLE agent_experience (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    pattern VARCHAR(256) NOT NULL,          -- 问题模式
    solution TEXT NOT NULL,                  -- 解决方案
    tags JSON,                              -- 标签
    score DECIMAL(3,2) DEFAULT 1.00,        -- 有用度评分（0.00-1.00）
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_user_id (user_id),
    INDEX idx_pattern (pattern)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

```python
class MySQLLongTermMemory(BaseMemory):
    def __init__(self, session_factory: Callable):
        self.db = session_factory

    async def save_message(self, session_id: str, msg: Message) -> None:
        async with self.db() as session:
            session.add(MessageRecord(
                session_id=session_id,
                role=msg.role,
                content=msg.content,
                metadata=msg.metadata,
            ))
            await session.commit()

    async def get_history(self, session_id: str, limit: int = 50) -> list[Message]:
        async with self.db() as session:
            rows = await session.execute(
                select(MessageRecord)
                .where(MessageRecord.session_id == session_id)
                .order_by(MessageRecord.created_at.desc())
                .limit(limit)
            )
            return [Message(role=r.role, content=r.content, metadata=r.metadata) for r in rows.scalars()]

    async def save_experience(self, user_id: str, pattern: str, solution: str, tags: list[str]) -> None:
        """保存经验知识：Agent 从成功执行中提取的通用解决方案。"""
        ...

    async def query_experience(self, pattern: str, top_k: int = 3) -> list[dict]:
        """检索相似经验：基于关键词匹配 + 标签匹配。"""
        ...
```

### 2.6 MemoryManager（统一入口）

```python
class MemoryManager:
    def __init__(self, short_term: BaseMemory, long_term: BaseMemory):
        self.short = short_term
        self.long = long_term

    # 写操作：同时写入短期 + 长期（异步，fire-and-forget）
    async def save_message(self, session_id: str, msg: Message) -> None:
        await self.short.save_message(session_id, msg)
        asyncio.create_task(self.long.save_message(session_id, msg))

    # 读操作：优先短期，miss 回退长期
    async def get_history(self, session_id: str, limit: int = 20) -> list[Message]:
        messages = await self.short.get_history(session_id, limit)
        if not messages:
            messages = await self.long.get_history(session_id, limit)
        return messages

    async def get_context(self, session_id: str) -> AgentState | None:
        state = await self.short.load(f"{session_id}:state")
        return state  # 短期为主，长期不存 state
```

### 2.7 配置

```python
class MemoryConfig(BaseSettings):
    short_term_provider: str = "redis"
    short_term_ttl: int = 3600
    long_term_provider: str = "mysql"
    max_history_per_session: int = 100
    enable_experience: bool = False                # 经验学习开关（默认关闭）
    experience_min_score: float = 0.5
    model_config = SettingsConfigDict(env_prefix="SA_MEMORY_")
```

---

## 3. Agent 可观测性

### 3.1 模块划分

```
src/super_agent/tracing/
├── __init__.py
├── setup.py                 # 已有：OpenTelemetry 初始化
├── metrics.py               # 已有：Prometheus 指标
└── agent_tracing.py         # 新增：Agent 执行追踪
```

### 3.2 追踪数据结构

```python
@dataclass
class AgentTrace:
    trace_id: str
    session_id: str
    user_id: str
    mode: AgentMode
    steps: list[TraceStep]
    total_duration_ms: float
    total_tokens: int
    total_cost: float
    status: Literal["success", "error", "blocked", "rejected"]
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class TraceStep:
    step_id: str
    step_type: Literal["llm_call", "tool_call", "classify", "plan", "retrieval", "guard_check"]
    input: str | None = None
    output: str | None = None
    duration_ms: float
    token_count: int = 0
    status: str = "success"
    error: str | None = None
```

### 3.3 AgentTracer

```python
class AgentTracer:
    """使用 OpenTelemetry span 记录 Agent 执行链路。"""

    @contextmanager
    def trace_agent_run(self, session_id: str, mode: AgentMode):
        with tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("session_id", session_id)
            span.set_attribute("mode", mode.value)
            yield span

    @contextmanager
    def trace_step(self, step_type: str, step_id: str):
        with tracer.start_as_current_span(f"agent.step.{step_type}") as span:
            span.set_attribute("step_id", step_id)
            span.set_attribute("step_type", step_type)
            yield span

    def record_llm_call(self, span, model: str, prompt_tokens: int, completion_tokens: int):
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.prompt_tokens", prompt_tokens)
        span.set_attribute("llm.completion_tokens", completion_tokens)
        span.set_attribute("llm.total_tokens", prompt_tokens + completion_tokens)
```

### 3.4 Agent 日志格式

每个 Agent 执行步骤输出结构化日志（JSON）：

```json
{
  "timestamp": "2026-07-23T10:00:00.123Z",
  "level": "INFO",
  "logger": "super_agent.agent",
  "trace_id": "trace-xxx",
  "session_id": "session-xxx",
  "step": {
    "type": "llm_call",
    "id": "step-1",
    "duration_ms": 1234,
    "token_count": 567,
    "model": "deepseek-v4-flash-202605"
  },
  "input_preview": "用户问题前100字符...",
  "output_preview": "LLM回答前100字符..."
}
```

### 3.5 Agent 日志管理器

```python
class AgentLogger:
    """结构化 Agent 日志，同时输出到 stdout（JSON）和 Redis。"""

    def log_step(self, trace_id: str, step: TraceStep) -> None:
        record = {
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "logger": "super_agent.agent",
            "trace_id": trace_id,
            "step": {
                "type": step.step_type,
                "id": step.step_id,
                "duration_ms": step.duration_ms,
                "token_count": step.token_count,
                "model": step.input if step.step_type == "llm_call" else None,
            },
            "input_preview": (step.input or "")[:100],
            "output_preview": (step.output or "")[:100],
        }
        logger.info(json.dumps(record, ensure_ascii=False))
```

### 3.6 关键指标（Prometheus）

在已有的 `metrics.py` 中新增指标：

```python
# Agent 执行计数
agent_runs_total = Counter("agent_runs_total", "Total Agent runs", ["mode", "status"])
agent_run_duration = Histogram("agent_run_duration_ms", "Agent run duration", ["mode"])

# 步骤级指标
agent_steps_total = Counter("agent_steps_total", "Total Agent steps", ["step_type", "status"])
agent_step_duration = Histogram("agent_step_duration_ms", "Agent step duration", ["step_type"])

# Token 消耗
agent_tokens_total = Counter("agent_tokens_total", "Total tokens consumed", ["mode", "model"])

# Guardrails 指标
guardrails_blocks_total = Counter("guardrails_blocks_total", "Guardrails blocks", ["guard_type"])
guardrails_check_duration = Histogram("guardrails_check_duration_ms", "Guard check duration")

# HITL 指标
hitl_tasks_total = Counter("hitl_tasks_total", "HITL tasks", ["status"])
hitl_approval_duration = Histogram("hitl_approval_duration_ms", "HITL approval duration")
```

### 3.7 审计日志

Agent 执行的审计数据写入 MySQL `agent_audit_log` 表：

```sql
CREATE TABLE agent_audit_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trace_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    mode VARCHAR(32) NOT NULL,
    query TEXT NOT NULL,
    response TEXT,
    total_duration_ms INT NOT NULL DEFAULT 0,
    total_tokens INT NOT NULL DEFAULT 0,
    total_cost DECIMAL(10,6) NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL,
    guardrails_result JSON,
    hitl_result JSON,
    step_count INT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_trace_id (trace_id),
    INDEX idx_session_id (session_id),
    INDEX idx_user_id (user_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.8 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/agent/trace/{trace_id}` | 获取 Agent 执行链路详情 |
| GET | `/agent/trace/list` | Agent 执行历史列表 `{session_id?, limit?}` |
| GET | `/agent/metrics` | Agent 实时指标（调用次数/延迟/Token） |

---

## 4. 配置

```python
class AgentTracingConfig(BaseSettings):
    enable_agent_tracing: bool = True
    log_detail_level: str = "summary"           # summary / detailed（detailed 记录完整 input/output）
    enable_audit: bool = True
    metrics_port: int = 8000                     # 复用 /metrics 端点
    model_config = SettingsConfigDict(env_prefix="SA_AGENT_TRACING_")
```

将 `MemoryConfig` 和 `AgentTracingConfig` 注册到 `Settings`：

```python
class Settings(BaseSettings):
    # ... 现有配置
    memory: MemoryConfig = MemoryConfig()
    agent_tracing: AgentTracingConfig = AgentTracingConfig()
```

---

## 5. 测试策略

| 模块 | 测试重点 |
|------|---------|
| RedisShortTermMemory | TTL 过期、消息队列 LRIM、并发读写 |
| MySQLLongTermMemory | 持久化、历史查询、经验存储/检索 |
| MemoryManager | 双写一致性、短期 miss → 长期回退 |
| AgentTracer | Span 嵌套、属性记录、性能影响 |
| AgentLogger | JSON 格式输出、敏感信息截断（100 字符） |
