# Phase 2 Part A 实现任务清单（8 项）

对照学习：设计文档 [2026-07-23-phase2-partA-agent-core-design.md](2026-07-23-phase2-partA-agent-core-design.md)

| # | 模块 | 文件 | 核心知识点 |
|---|------|------|-----------|
| 1 | **配置类** | `config.py` | Pydantic Settings 分层配置、`env_prefix`、`extra="ignore"` |
| 2 | **分类器** | `core/classifier.py` | 三层分类（规则→Embedding→LLM）、5分钟缓存、三维度输出(intent/risk/complexity) |
| 3 | **PlanExecute** | `core/plan_execute.py` | Planner→Executor→Re-planner三步流程、JSON步骤结构化、依赖检查 |
| 4 | **HITL审批** | `core/plan_execute.py` | 创建审批→等待→审批/驳回/超时、轮询模式 |
| 5 | **执行引擎** | `core/runtime.py` | 四条执行路径(qa/knowledge/action-simple/action-multi)、ReAct循环、能力按需加载 |
| 6 | **安全护栏** | `core/guardrails.py` | Fail-close、注入检测、用户三级策略（匿名/超管/普通）、输出敏感信息掩码 |
| 7 | **状态管理** | `core/state.py` | 内存→Redis→MySQL三层存储、Hash+Stream持久化、服务重启恢复 |
| 8 | **API入口** | `api/agent.py` | 全链路串联、SSE流式推送、会话管理、审批API |
| 9 | **前端视觉** | `static/agent.html` | `case 'source'` SSE事件处理、`llm-fallback` CSS淡黄底色 |

## 建议学习顺序（按代码复杂度递增）

```
1. config.py          ← 5分钟：看懂所有配置项
2. core/models.py     ← 5分钟：核心数据模型
3. classifier.py      ← 15分钟：最简单的业务模块
4. guardrails.py      ← 15分钟：安全设计模式
5. state.py           ← 20分钟：数据流动的核心
6. runtime.py         ← 40分钟：最复杂，四条路径+ReAct
7. plan_execute.py    ← 30分钟：复杂任务编排
8. agent.py           ← 15分钟：全链路串联
```
