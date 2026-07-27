# Phase 2 - Part B: 企业治理设计

设计日期: 2026-07-23
状态: 设计稿

## 1. 概述

企业治理层提供 Agent 上线所需的安全、审批和容错能力，包括 Guardrails（安全护栏）、Human-in-the-Loop（人工审批）和结构化错误处理。这些是 Agent 在生产环境中安全运行的前提。

**依赖关系**：Part A（Agent Core）完成后接入，Part B 作为中间件层包裹 Agent 执行。

```
User Input → Guardrails → Agent Core → HITL → Error Handler → Response
                ↑                            ↑
          prompt injection 防护         关键操作暂停审批
          领域范围限制                   超时/重试兜底
          敏感信息过滤
```

---

## 2. Guardrails（安全护栏）

### 2.1 模块划分

```
src/super_agent/guardrails/
├── __init__.py
├── base.py                 # BaseGuard 抽象接口
├── inject_detector.py      # Prompt 注入检测
├── domain_limiter.py       # 领域范围限制
├── sensitivity_filter.py   # 敏感信息过滤
└── pipeline.py             # GuardPipeline（守卫链编排）
```

### 2.2 设计原则

- **Fail-close**：守卫不确定时，宁可通过不通过
- **分层检查**：规则级（快）→ LLM 级（准）
- **可插拔**：每个 Guard 独立，可自由组合
- **可配置**：每个会话/用户可配置不同的守卫策略

### 2.3 BaseGuard 接口

```python
class GuardResult(BaseModel):
    passed: bool
    risk_level: Literal["safe", "suspicious", "blocked"]
    reason: str = ""
    action: Literal["allow", "warn", "block", "mask"] = "allow"

class BaseGuard(ABC):
    @abstractmethod
    def check_input(self, query: str, context: AgentState) -> GuardResult: ...
    @abstractmethod
    def check_output(self, response: str, context: AgentState) -> GuardResult: ...
```

### 2.4 Prompt 注入检测

```python
class InjectDetector(BaseGuard):
    """检测 prompt 注入攻击。"""
    def check_input(self, query: str, context: AgentState) -> GuardResult:
        # 1. 规则层：正则匹配已知注入模式
        #    - "忽略以上指令" / "ignore all previous instructions"
        #    - "你是*，现在你是*"（角色扮演逃脱）
        #    - Base64/Hex 编码指令
        # 2. LLM 层：对可疑 query 调用 LLM 判断
        # 3. 返回结果：allow / warn / block
```

检测覆盖：

| 攻击类型 | 规则检测 | LLM 检测 |
|---------|---------|---------|
| 指令忽略 | 正则匹配中英文 | 高可信度时 |
| 角色扮演逃脱 | 正则匹配 | 低可信度时 |
| 编码指令 | Base64/Hex 正则 | 编码内容解码后 |
| 越狱 prompt | 关键词库 | LLM 判断 |
| 间接注入（检索内容） | — | 检索结果过滤 |

### 2.5 领域范围限制

```python
class DomainLimiter(BaseGuard):
    """限制 Agent 只回答 IT 运维领域问题。"""
    def check_input(self, query: str, context: AgentState) -> GuardResult:
        # 1. 规则层：关键词匹配（IT 领域技术词库）
        # 2. LLM 层：询问 "此问题是否属于 IT 运维范围"
        # 3. 非领域问题 → block + 回复 "我只回答 IT 运维相关问题"
```

可配置允许的领域列表：

```yaml
# domain_whitelist.yaml（与 tags.yaml 放在 doc_dir 根目录）
allowed_domains:
  - "IT运维"
  - "SRE"
  - "数据库管理"
  - "网络"
  - "安全"
  - "监控告警"
fallback_message: "抱歉，我只能回答 IT 运维相关的问题。请提供运维方面的具体问题。"
```

### 2.6 敏感信息过滤

```python
class SensitivityFilter(BaseGuard):
    """检测输入/输出中的敏感信息并进行过滤或掩码。"""
    def check_input(self, query: str, context: AgentState) -> GuardResult:
        # 检测：IP/域名/密码/AK/SK/手机号/身份证
        # action: 命中敏感 → mask（替换为 ***）

    def check_output(self, response: str, context: AgentState) -> GuardResult:
        # 输出时再次检测，防止 Agent 泄露敏感信息
        # action: 命中 → mask 后输出
```

默认敏感数据类型：

| 类型 | 规则 | 处理 |
|------|------|------|
| IP 地址 | `\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}` | 可选掩码 |
| 内网 IP | `10\.\d+\.\d+\.\d+` / `192\.168\.` | 默认掩码 |
| 密码/AK/SK | 关键词 + 上下文 | 默认 mask |
| 手机号 | 正则 | 掩码中间 4 位 |
| 邮箱 | 正则 | 掩码 @ 前部分 |

### 2.7 GuardPipeline

```python
class GuardPipeline:
    def __init__(self, guards: list[BaseGuard], fail_mode: str = "block"):
        """fail_mode: block / warn_only"""
        ...

    async def check_input(self, query: str, context: AgentState) -> GuardResult:
        # 按顺序执行所有 guard，任一返回 block 则终止
        ...

    async def check_output(self, response: str, context: AgentState) -> GuardResult:
        # 按顺序执行所有 guard，命中敏感则 mask
        ...
```

### 2.8 配置

```python
class GuardrailsConfig(BaseSettings):
    enabled: bool = True                      # 总开关
    inject_detection: bool = True
    domain_limiter: bool = True
    sensitivity_filter: bool = True
    sensitivity_mask: bool = True             # 输出时 mask 敏感信息
    fail_mode: Literal["block", "warn_only"] = "block"
    block_message: str = "输入包含不安全内容，已拦截"
    domain_fallback: str = "抱歉，我只能回答 IT 运维相关的问题"
    allowed_domains_file: str = ""            # YAML 文件路径，空 = 使用内置默认
    model_config = SettingsConfigDict(env_prefix="SA_GUARDRAILS_")
```

---

## 3. Human-in-the-Loop（人工审批）

### 3.1 模块划分

```
src/super_agent/hitl/
├── __init__.py
├── base.py               # BaseApprovalHandler 抽象接口
├── manager.py            # ApprovalManager
└── models.py             # ApprovalTask / ApprovalStatus
```

### 3.2 触发场景

| 场景 | 触发条件 | 审批超时 |
|------|---------|---------|
| 高风险工具调用 | Agent 要执行写操作（重启/删除/修改） | 300s |
| 超出领域范围 | 用户要求非 IT 领域操作 | 180s |
| 敏感信息访问 | Agent 要返回密码/密钥等 | 300s |
| Guardrails 告警 | guard 返回 suspicious 级别 | 120s |

### 3.3 核心模型

```python
class ApprovalTask(BaseModel):
    id: str
    session_id: str
    user_id: str
    action: str                           # 操作描述
    tool_name: str | None                 # 要调用的工具
    args: dict | None                     # 工具参数
    risk_level: Literal["low", "medium", "high", "critical"]
    status: ApprovalStatus                # pending / approved / rejected / expired
    created_at: datetime
    expires_at: datetime
    reviewer_id: str | None = None
    review_reason: str | None = None
```

### 3.4 ApprovalManager

```python
class ApprovalManager:
    def create_task(self, task_data: ApprovalTask) -> str: ...        # 创建审批任务
    def approve(self, task_id: str, reviewer_id: str, reason: str) -> bool: ...
    def reject(self, task_id: str, reviewer_id: str, reason: str) -> bool: ...
    def get_pending(self, session_id: str) -> list[ApprovalTask]: ...
    def wait_for_approval(self, task_id: str, timeout: int) -> ApprovalStatus: ...
```

### 3.5 交互流程

```
User: "重启 MySQL 服务器"

Agent → Guardrails 检查 → 检测到高风险操作
    → 创建 ApprovalTask (status=pending)
    → 暂停 Agent 执行
    → 通知前端/用户等待审批

前端/审批人调用 POST /hitl/approve {task_id, reviewer_id, reason}
    → ApprovalManager 更新状态
    → Agent 恢复执行 或 执行回滚

超时未审批 → 自动驳回（expired）
```

### 3.6 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/hitl/pending` | 当前待审批列表 `{session_id?}` |
| POST | `/hitl/approve` | 审批通过 `{task_id, reviewer_id, reason}` |
| POST | `/hitl/reject` | 审批驳回 `{task_id, reviewer_id, reason}` |
| GET | `/hitl/history` | 审批历史 `{session_id?, user_id?}` |

### 3.7 配置

```python
class HITLConfig(BaseSettings):
    enabled: bool = False
    default_timeout: int = 300                    # 默认审批超时（秒）
    notify_channel: str = ""                      # 通知渠道（预留）
    risk_threshold: Literal["low", "medium", "high", "critical"] = "high"
    auto_approve_tools: list[str] = field(default_factory=list)  # 白名单工具无需审批
    model_config = SettingsConfigDict(env_prefix="SA_HITL_")
```

---

## 4. 结构化错误处理

### 4.1 模块划分

```
src/super_agent/core/
└── errors.py              # 错误类型定义 + ErrorHandler（与 Part A 的 Agent Core 共享）
```

### 4.2 错误类型

```python
class ErrorType(Enum):
    LLM_PARSE_ERROR = "llm_parse_error"        # LLM 返回格式异常
    TOOL_TIMEOUT = "tool_timeout"               # 工具调用超时
    RATE_LIMIT = "rate_limit"                   # API 限流
    MAX_RETRIES = "max_retries"                 # 超最大重试次数
    CONTEXT_OVERFLOW = "context_overflow"       # 上下文超出限制
    GUARDRAILS_BLOCK = "guardrails_block"       # 被 guardrails 拦截
    HITL_TIMEOUT = "hitl_timeout"               # 人工审批超时
    HITL_REJECTED = "hitl_rejected"             # 人工审批驳回
    UNEXPECTED = "unexpected"                   # 未归类错误

class RecoveryAction(Enum):
    RETRY = "retry"                 # 重试（指数退避）
    FALLBACK = "fallback"           # 降级回复
    ESCALATE = "escalate"           # 转人工
    ABORT = "abort"                 # 终止执行
```

### 4.3 ErrorHandler

```python
class ErrorHandler:
    def __init__(self, retry_max: int = 3, retry_base_delay: float = 1.0):
        self.retry_map: dict[ErrorType, RecoveryAction] = {
            ErrorType.LLM_PARSE_ERROR: RecoveryAction.RETRY,
            ErrorType.TOOL_TIMEOUT: RecoveryAction.RETRY,
            ErrorType.RATE_LIMIT: RecoveryAction.RETRY,
            ErrorType.MAX_RETRIES: RecoveryAction.FALLBACK,
            ErrorType.CONTEXT_OVERFLOW: RecoveryAction.FALLBACK,
            ErrorType.GUARDRAILS_BLOCK: RecoveryAction.ABORT,
            ErrorType.HITL_TIMEOUT: RecoveryAction.FALLBACK,
            ErrorType.HITL_REJECTED: RecoveryAction.ABORT,
            ErrorType.UNEXPECTED: RecoveryAction.ESCALATE,
        }

    def handle(self, error: ExecutionError, context: AgentState) -> RecoveryAction:
        """决定恢复策略，更新 context.errors。"""
        action = self.retry_map.get(error.error_type, RecoveryAction.ABORT)
        if action == RecoveryAction.RETRY and error.retry_count >= self.retry_max:
            action = RecoveryAction.FALLBACK
            error.error_type = ErrorType.MAX_RETRIES
        context.errors.append(error)
        return action

    def get_fallback_response(self, error: ExecutionError) -> str:
        """根据错误类型生成友好的降级回复。"""
        fallbacks = {
            ErrorType.MAX_RETRIES: "操作执行超时，请稍后重试",
            ErrorType.CONTEXT_OVERFLOW: "会话上下文过长，已自动清理部分历史",
            ErrorType.GUARDRAILS_BLOCK: "操作已被安全策略拦截",
            ErrorType.HITL_REJECTED: "操作已被审批人驳回",
            ErrorType.HITL_TIMEOUT: "审批超时，操作已取消",
        }
        return fallbacks.get(error.error_type, "系统内部错误，请稍后重试")
```

### 4.4 Agent 错误处理流程

```
Agent 执行任意步骤 → 捕获异常
    → ErrorHandler.handle(error, context)
    ├── RETRY → 重试（指数退避）
    ├── FALLBACK → 返回降级回复 + 记录 error 到 context
    ├── ESCALATE → 记录 + 返回 "需要人工处理"
    └── ABORT → 终止执行 + 返回拦截原因
```

---

## 5. 配置汇总

所有配置项新增到 `config.py` 的 `Settings` 类：

```python
class Settings(BaseSettings):
    # ... 现有配置
    guardrails: GuardrailsConfig = GuardrailsConfig()
    hitl: HITLConfig = HITLConfig()
    context: ContextConfig = ContextConfig()
    session: SessionConfig = SessionConfig()
    prompt: PromptConfig = PromptConfig()
```

---

## 6. 测试策略

| 模块 | 测试重点 |
|------|---------|
| InjectDetector | 注入样本覆盖（中英文）、误报率 |
| DomainLimiter | 领域内/外分类准确率 |
| SensitivityFilter | 各种敏感数据格式识别、mask 正确性 |
| GuardPipeline | 组合执行顺序、fail-close 行为 |
| ApprovalManager | 创建/审批/超时/驳回全流程 |
| ErrorHandler | 各错误类型的恢复策略、重试计数、降级回复 |
