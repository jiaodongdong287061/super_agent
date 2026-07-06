# Agent 用户权限架构设计

## 1. 背景与目标

### 1.1 问题描述

企业级 Agent 系统需要对接多个后端业务系统（如监控系统、CMDB、CI/CD 平台、云平台等）。这些业务系统本身已有成熟的用户权限体系，Agent 作为上层入口，必须解决以下矛盾：

- **不能另起炉灶**：Agent 不应构建一套与业务系统独立、可能冲突的权限体系
- **需要预检**：Agent 在调用下游 API 前，需要知道用户是否有权限，否则浪费 LLM Token 和时间
- **业务系统权限模型各异**：有的用 RBAC，有的用 ACL，有的用自定义规则

### 1.2 核心原则

**权限与业务系统一致**——Agent 不做自己的那套权限定义，而是对齐真实业务系统的权限模型。

| 原则 | 说明 |
|------|------|
| 权限源不迁移 | 权限的定义和存储仍在各业务系统，Agent 不存权限副本 |
| 不重复造轮子 | 参数级、资源级权限由下游系统自己校验 |
| 预检而非猜测 | Agent 在可见性层面做预检，避免无效调用 |

### 1.3 前置条件

- 企业有统一身份认证基础设施（LDAP/OAuth2/OIDC），各业务系统已接入同一 IdP
- Agent 通过 API（REST/gRPC）对接后端业务系统
- 同一批用户通过统一入口访问 Agent

---

## 2. 架构总览

### 2.1 核心思路：URL 作为统一权限语言

不使用各业务系统内部的权限模型表示（如 `role:admin`、`permission:server.reboot` 等），而是使用 **HTTP Method + URL** 作为跨系统的统一语言：

- **业务系统侧**：接收 Token → 返回该 Token 有权限调用的 URL 列表
- **Agent 侧**：每个 Tool 声明它依赖哪些 URL（含 Method）
- **比对**：会话启动时比对两者，生成当前用户的 Tool Manifest

```
         ┌─────────────┐
         │   用户登录    │
         └──────┬──────┘
                │ OAuth2 Token
         ┌──────▼──────┐
         │  Agent 入口  │
         └──────┬──────┘
                │
    ┌───────────┴───────────┐
    │                       │
    ▼                       ▼
┌──────────────┐    ┌──────────────┐
│ 业务系统 A    │    │ 业务系统 B    │
│ 返回可用 URL  │    │ 返回可用 URL  │
│ 列表          │    │ 列表          │
└──────┬───────┘    └──────┬───────┘
        │                  │
        └────────┬─────────┘
                 │
          ┌──────▼──────┐
          │  URL 比对层   │ ← Tool 声明的 URL 需求 vs 用户实际可用 URL
          └──────┬──────┘
                 │
          ┌──────▼──────┐
          │ Tool Manifest│ ← 当前用户可用的 Tool 列表（含能力子集）
          └──────┬──────┘
                 │
          ┌──────▼──────┐
          │  LLM 侧      │ ← 只看到 Manifest 内的 Tool
          └─────────────┘
```

### 2.2 数据流

```
用户登录
  │
  ├─ 1. IdP 认证 → 获取 OAuth2 Token（含用户身份）
  │
  ├─ 2. Agent 携带 Token 向各已注册业务系统查询
  │       GET /__capabilities   (每个业务系统需暴露的轻量接口)
  │       返回: ["GET:/api/v1/servers/*", "POST:/api/v1/servers/{id}/reboot", ...]
  │       注：此接口只返回 URL 列表，不暴露内部权限模型
  │
  ├─ 3. Agent 汇总所有业务系统返回的 URL → 当前用户的 Capability Profile
  │
  ├─ 4. 遍历已注册的 Tool 列表
  │      每个 Tool 声明其依赖的 URL 集合（含必需/可选标记）
  │      比对 Tool.required_urls ⊆ Capability Profile
  │
  ├─ 5. 生成该用户的 Tool Manifest
  │      - 可用 Tool 列表（所有必需 URL 满足的 Tool）
  │      - 各 Tool 的能力子集（标记哪些可选 URL 可用）
  │
  └─ 6. LLM 加载 Manifest，只看到用户有权使用的 Tool
         Prompt 中注明可选 URL 的状态，引导 LLM 的行为
```

---

## 3. 详细设计

### 3.1 URL 权限表示规范

以 `{HTTP Method}:{URL Pattern}` 作为统一的权限描述单位。

**URL Pattern 支持通配符**：

| 模式 | 示例 | 说明 |
|------|------|------|
| 精确匹配 | `GET:/api/v1/servers/server-01` | 精确到具体资源 |
| 单级通配 | `GET:/api/v1/servers/*` | 匹配 servers 下的任意资源 |
| 多级通配 | `GET:/api/v1/*` | 匹配 api/v1 下的所有路径 |
| 参数通配 | `POST:/api/v1/servers/{id}/reboot` | `{id}` 匹配任意值 |

**Method + URL 必须成对出现**，因为同一 URL 不同 Method 的权限不同：

```
# 用户 A 的业务系统返回：
GET:/api/v1/servers/*
POST:/api/v1/servers/{id}/reboot

# 用户 B 的业务系统返回（只读权限）：
GET:/api/v1/servers/*
```

**比对规则**：

1. Tool 声明的 URL 逐一与 Capability Profile 匹配
2. 匹配时：Method 精确匹配，URL 支持通配符匹配
3. 所有必需 URL 匹配 → Tool 可用
4. 部分可选 URL 匹配 → Tool 可用但标记能力子集

### 3.2 Tool 定义结构

每个 Tool 在注册/定义时声明其依赖的 URL：

```python
@dataclass
class ToolCapability:
    method: str              # HTTP Method: GET/POST/PUT/DELETE
    url_pattern: str         # URL 模式，支持通配
    required: bool           # True=必需 / False=可选
    description: str         # 该 URL 在 Tool 中的作用描述（供 LLM 理解）

@dataclass
class ToolDefinition:
    name: str                # Tool 名称
    description: str         # Tool 功能描述
    capabilities: list[ToolCapability]  # 依赖的 URL 清单
```

**示例**：

```python
health_check_tool = ToolDefinition(
    name="server_health_check",
    description="检查服务器健康状态，包括 CPU、内存、磁盘指标",
    capabilities=[
        ToolCapability("GET", "/api/v1/metrics/cpu/*", required=True, 
                       description="获取 CPU 指标"),
        ToolCapability("GET", "/api/v1/metrics/memory/*", required=True,
                       description="获取内存指标"),
        ToolCapability("GET", "/api/v1/metrics/disk/*", required=True,
                       description="获取磁盘指标"),
        ToolCapability("POST", "/api/v1/servers/{id}/action", required=False,
                       description="执行操作（如重启、扩容），仅查询时无需此权限"),
    ]
)

ci_deploy_tool = ToolDefinition(
    name="ci_deploy",
    description="在 Jenkins 上执行构建和部署",
    capabilities=[
        ToolCapability("GET", "/jenkins/api/*", required=True,
                       description="查看 Jenkins 项目和构建状态"),
        ToolCapability("POST", "/jenkins/api/jobs/{name}/build", required=True,
                       description="触发构建任务"),
        ToolCapability("POST", "/jenkins/api/jobs/{name}/config", required=False,
                       description="修改 Jenkins Job 配置"),
    ]
)
```

### 3.3 业务系统 Capability 接口规范

每个业务系统必须暴露一个轻量级的 Capability 查询接口：

```
GET /__capabilities
Authorization: Bearer <user_token>

Response 200:
{
  "system": "monitoring-platform",
  "capabilities": [
    "GET:/api/v1/metrics/cpu/*",
    "GET:/api/v1/metrics/memory/*",
    "GET:/api/v1/metrics/disk/*"
  ]
}
```

设计要点：

| 要点 | 说明 |
|------|------|
| **接口轻量** | 只返回 URL 列表，不涉及内部权限模型细节 |
| **透传 Token** | 业务系统直接用 Token 查询自己的权限引擎，不暴露内部逻辑 |
| **静态或动态** | 可以是预计算清单（快）或实时查询（准），由业务系统自定 |
| **缓存** | Agent 侧可缓存此结果（按 Token+TTL），减少重复查询 |
| **统一路径** | 所有业务系统统一使用 `GET /__capabilities` 路径 |

### 3.4 Agent 侧 URL 比对层

```python
class CapabilityMatcher:
    """URL 比对引擎：匹配 Tool 声明的 URL 与用户实际可用的 URL"""

    def __init__(self, user_capabilities: list[str]):
        # user_capabilities 是从所有业务系统汇总的 URL 列表
        # ["GET:/api/v1/metrics/cpu/*", "POST:/jenkins/api/jobs/*/build", ...]
        self._capabilities = user_capabilities

    def match(self, tool: ToolDefinition) -> ToolMatchResult:
        """比对 Tool 的所有 URL 依赖与用户能力"""
        required_matched = []
        required_missing = []
        optional_matched = []
        optional_missing = []

        for cap in tool.capabilities:
            matched = self._url_matches(cap.method, cap.url_pattern)
            if cap.required:
                (required_matched if matched else required_missing).append(cap)
            else:
                (optional_matched if matched else optional_missing).append(cap)

        return ToolMatchResult(
            tool_name=tool.name,
            available=len(required_missing) == 0,  # 必需 URL 全部匹配才可用
            missing_required=required_missing,
            available_optional=optional_matched,
            missing_optional=optional_missing,
        )

    def _url_matches(self, method: str, url_pattern: str) -> bool:
        """单条 URL 匹配，支持通配符"""
        target = f"{method.upper()}:{url_pattern}"
        for cap in self._capabilities:
            if self._pattern_match(target, cap):
                return True
        return False

    def _pattern_match(self, pattern: str, target: str) -> bool:
        """通配符匹配: {id} 匹配任意段，* 匹配任意路径"""
        import fnmatch
        # 将 {param} 转换为 * 通配
        pattern = re.sub(r'\{[^}]+\}', '*', pattern)
        return fnmatch.fnmatch(target, pattern)
```

### 3.5 Tool Manifest 生成

比对完成后，生成当前用户的 Tool Manifest：

```python
@dataclass
class ToolManifestEntry:
    name: str
    description: str
    available: bool                       # 是否可用（必需 URL 全部满足）
    capability_hints: list[str]           # 可选能力的可用状态（供 LLM 参考）

@dataclass
class ToolManifest:
    user_id: str
    entries: list[ToolManifestEntry]
    generated_at: datetime
```

**对 LLM 侧的呈现**：

```json
{
  "available_tools": [
    {
      "name": "server_health_check",
      "description": "检查服务器健康状态，包括 CPU、内存、磁盘指标",
      "capabilities": ["查看指标", "执行操作"]  
      // "执行操作" 标记为可用，LLM 知道用户能做操作
    },
    {
      "name": "server_health_check",
      "description": "检查服务器健康状态，包括 CPU、内存、磁盘指标",
      "capabilities": ["查看指标"]
      // "执行操作" 未出现，LLM 知道只能查不能改
    }
  ]
}
```

### 3.6 会话生命周期

```
┌──────────────────────────────────────────────────┐
│                  用户会话生命周期                     │
├──────────────────────────────────────────────────┤
│                                                    │
│  登录 ────────────────────────────────────────────┐ │
│     │                                              │ │
│     ├─ 获取 Token（OAuth2 / OIDC）                   │ │
│     ├─ 查询各业务系统 Capabilities                    │ │
│     ├─ 生成 Capability Profile（缓存 TTL=5min）       │ │
│     ├─ 比对所有 Tool → 生成 Tool Manifest             │ │
│     └─ LLM 加载 Manifest                             │ │
│                                                    │ │
│  会话进行中 ────────────────────────────────────── │ │
│     │                                              │ │
│     ├─ LLM 根据 Manifest 选择 Tool 调用               │ │
│     ├─ 调用时使用用户 Token 透传到下游业务系统          │ │
│     ├─ 下游系统自行做参数级/资源级权限校验              │ │
│     └─ 结果返回给 LLM 生成回答                        │ │
│                                                    │ │
│  Token 过期 ───────────────────────────────────── │ │
│     │                                              │ │
│     ├─ 提示用户重新登录 / 刷新 Token                  │ │
│     └─ 清除 Capability Profile 缓存                  │ │
│                                                    │ │
└──────────────────────────────────────────────────┘
```

---

## 4. 与其他模块的关系

### 4.1 与 MCP 的关系

MCP Server 注册的外部工具同样需要声明 Capability URL：

```python
class MCPToolAdapter(ToolDefinition):
    """将 MCP 工具包装为标准 ToolDefinition"""
    # MCP 工具注册时，由管理员声明其对哪些 URL 的依赖
    # 或由 MCP discovery 阶段自动推断
```

### 4.2 与知识库（RAG）的关系

知识库检索本身不涉及权限——知识库是静态文档的索引。

但检索结果的可视性可能需要权限控制（比如某个 runbook 属于敏感系统），可在检索时增加 metadata 过滤条件，基于用户的 Capability Profile 自动注入过滤条件。

### 4.3 与沙箱的关系

沙箱执行的代码可能调用外部 API。如果沙箱内代码使用用户 Token，需注意 Token 泄露风险。建议沙箱内使用代理身份（Service Account + 用户上下文的 Scope 限制）。

---

## 5. 边界情况

| 场景 | 处理方式 |
|------|---------|
| 业务系统不可用/超时 | Tool Manifest 对该系统的所有 URL 标记为"不可用"，降级处理 |
| 增量权限变更 | 会话内权限不变，下次登录重新查询 Capability |
| 通配 URL 过度开放 | 遵循业务系统自身的最小权限原则，Agent 不额外收紧 |
| 无任何可用 URL | 用户只能使用纯 LLM 能力（如通用问答），无业务系统操作能力 |
| 业务系统新增 URL | 新 URL 不在 Capability Profile 中 → 比对失败 → Tool 不可用，等待下次 Token 刷新 |

---

## 6. 落地建议

### 6.1 业务系统接入清单

| 阶段 | 接入系统 | 优先度 |
|------|---------|--------|
| Phase 1 | 实现 Agent 侧 CapabilityMatcher + Tool 定义规范 | 高 |
| Phase 2 | 首个业务系统接入 + 暴露 `__capabilities` 接口 | 高 |
| Phase 3 | 更多业务系统接入 + Manifest 缓存优化 | 中 |

### 6.2 关键指标

| 指标 | 目标 |
|------|------|
| Manifest 生成延迟（查询所有业务系统） | 20 系统 ≤ 500ms |
| URL 比对延迟（单会话第一次） | ≤ 10ms |
| 误放率（Agent 认为可用但下游 403） | ≤ 0.1% |
| 误拦率（Agent 认为不可用但下游 200） | ≤ 0.1% |
