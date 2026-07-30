"""
PlanExecute 管线 — 复杂任务的多步编排引擎。

=== 设计思路 ===
PlanExecute 不是独立的 Agent，而是 AgentRuntime 的"可选升级包"。
当 Classifier 判定 complexity=multi_step 时才启用。

=== 流程 ===
1. Planner（规划器）：LLM 把用户请求拆成有序步骤
2. Executor（执行器）：按顺序执行每一步
3. Re-planner（重规划器）：每步完成后检查结果，必要时调整后续计划

=== 和普通 ReAct 的区别 ===
| 维度 | ReAct | PlanExecute |
|------|-------|-------------|
| 计划 | 边做边想，没有预设计划 | 先计划后执行 |
| 适用 | 1-3 步的简单任务 | 3+ 步的复杂任务 |
| 可预见性 | 不可预知下一步 | 提前看到完整计划 |
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from super_agent.config import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════

@dataclass
class PlannedStep:
    """
    计划中的一步。

    Attributes:
        id: 步骤唯一标识，如 "step_1"
        description: 步骤描述，给人看的
        action: 操作类型
            - tool_call: 调工具
            - llm_analysis: LLM 分析
            - skill: 调用技能
        action_params: 操作参数，如 {"tool": "mysql_query", "args": {...}}
        depends_on: 依赖的上一步 ID 列表，空列表表示无依赖
        expected_output: 期望输出的说明，LLM 据此判断是否达标
        status: 执行状态
        result: 实际执行结果
    """
    id: str
    description: str
    action: Literal["tool_call", "llm_analysis", "skill"]
    action_params: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    expected_output: str = ""
    status: Literal["pending", "running", "success", "failed", "skipped"] = "pending"
    result: str = ""


@dataclass
class Plan:
    """完整的执行计划，包含有序步骤列表。"""
    steps: list[PlannedStep] = field(default_factory=list)
    current_step: int = 0
    original_query: str = ""  # 原始用户问题，用于语境参考


@dataclass
class PlanResult:
    """PlanExecute 执行结果。"""
    success: bool
    steps_completed: int
    summary: str
    details: list[dict] = field(default_factory=list)


# ═══════════════════════════════════════════════
# PlanExecute 管线
# ═══════════════════════════════════════════════

class PlanExecute:
    """
    复杂任务管线：拆步骤 → 执行 → 动态调整。

    Usage:
        pipeline = PlanExecute(tools=tools_list, llm_client=client)
        result = await pipeline.execute(query="排查 MySQL 主从延迟并修复")

    三步循环：
        1. Planner:    LLM 分析任务，生成有序步骤列表
        2. Executor:   按顺序执行每一步（工具调用 / LLM 分析）
        3. Re-planner: 每步完成后检查结果，必要时调整计划
    """

    def __init__(self, tools: list | None = None, llm_client: httpx.AsyncClient | None = None):
        self.tools = tools or []
        self._client = llm_client
        # LLM 调用配置（从 settings 读取）
        cfg = settings.llm
        self._base_url = cfg.oneapi_base_url.rstrip("/")
        self._api_key = cfg.oneapi_api_key
        self._model = cfg.default_model
        self._max_steps = settings.runtime.max_steps

    # ── 公开入口 ──

    async def execute(
        self,
        query: str,
        user_info: dict | None = None,
    ) -> PlanResult:
        """
        执行 PlanExecute 全流程。

        Args:
            query: 用户原始问题
            user_info: 用户上下文（角色、部门等），供工具调用时鉴权

        Returns:
            PlanResult: 包含执行结果和详细步骤记录
        """
        # 第 1 步：Planner — 拆解任务
        plan = await self._plan(query)
        logger.info("Plan created: %d steps for query: %s", len(plan.steps), query[:80])

        details = []

        # 第 2 步：Executor — 逐步执行
        while plan.current_step < len(plan.steps):
            step = plan.steps[plan.current_step]

            # 检查依赖是否满足
            if not self._deps_met(step, plan.steps):
                logger.info("Skipping step %s: dependencies not met", step.id)
                step.status = "skipped"
                plan.current_step += 1
                continue

            # 执行当前步骤
            step.status = "running"
            logger.info("Executing step %s: %s", step.id, step.description)

            result = await self._execute_step(step, user_info)
            step.result = result
            step.status = "success"

            details.append({
                "step_id": step.id,
                "description": step.description,
                "action": step.action,
                "result": result[:200] if result else "",
            })

            # 第 3 步：Re-planner — 检查结果，必要时调整计划
            plan = await self._replan(plan, step, query)

            plan.current_step += 1

        steps_done = sum(1 for s in plan.steps if s.status == "success")
        return PlanResult(
            success=steps_done > 0,
            steps_completed=steps_done,
            summary=f"共 {len(plan.steps)} 步，完成 {steps_done} 步",
            details=details,
        )

    # ── 流式版本（逐步产出事件） ──

    async def execute_stream(
        self,
        query: str,
        user_info: dict | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        流式版本的 PlanExecute，逐步产出事件。

        产出的事件类型：
        - plan_created: 计划生成完成，含步骤列表
        - step_start:  开始执行某一步
        - tool_call:   工具调用开始
        - tool_result: 工具返回结果
        - step_done:   某一步完成
        - replan:      计划被调整
        - done:        全部完成
        - error:       执行出错
        """
        plan = await self._plan(query)
        yield {"type": "plan_created", "steps": [s.id for s in plan.steps]}

        while plan.current_step < len(plan.steps):
            step = plan.steps[plan.current_step]

            if not self._deps_met(step, plan.steps):
                step.status = "skipped"
                plan.current_step += 1
                continue

            step.status = "running"
            yield {"type": "step_start", "step_id": step.id, "description": step.description}

            try:
                result = await self._execute_step(step, user_info)
                step.result = result
                step.status = "success"
                yield {"type": "step_done", "step_id": step.id, "result": result[:500]}
            except Exception as e:
                step.status = "failed"
                step.result = str(e)
                yield {"type": "step_error", "step_id": step.id, "error": str(e)}
                break

            # Re-planner
            new_plan = await self._replan(plan, step, query)
            if len(new_plan.steps) != len(plan.steps):
                yield {"type": "replan", "old_steps": len(plan.steps), "new_steps": len(new_plan.steps)}
                plan = new_plan

            plan.current_step += 1

        steps_done = sum(1 for s in plan.steps if s.status == "success")
        yield {
            "type": "done",
            "success": steps_done > 0,
            "summary": f"共 {len(plan.steps)} 步，完成 {steps_done} 步",
        }

    # ═══════════════════════════════════════════════
    # 内部方法：Planner / Executor / Re-planner
    # ═══════════════════════════════════════════════

    async def _plan(self, query: str) -> Plan:
        """
        Planner：LLM 把用户请求拆解为有序步骤。

        给 LLM 的 prompt 要求它输出 JSON 格式的步骤列表，
        每步包含：id、描述、操作类型、参数、依赖。
        """
        tools_desc = "\n".join(
            f"- {t.name}: {t.description}"
            for t in (self.tools or [])
        )
        prompt = f"""你是一个任务规划专家。请将用户的任务拆解为有序的执行步骤。

用户任务: {query}

{'可用工具:\n' + tools_desc if tools_desc else '没有可用工具，只能做 LLM 分析。'}

要求：
1. 每步只做一件事
2. 明确步骤间的依赖关系
3. 输出 JSON 数组格式

输出格式:
[
  {{
    "id": "step_1",
    "description": "执行 show slave status 查看主从状态",
    "action": "tool_call",
    "action_params": {{"tool": "mysql_query", "args": {{"sql": "SHOW SLAVE STATUS"}}}},
    "depends_on": [],
    "expected_output": "主从复制状态信息"
  }}
]

action 只能是: tool_call（调用工具）、llm_analysis（LLM 分析）、skill（调用技能）
如果不需要工具，用 llm_analysis。"""

        content = await self._call_llm(prompt)
        try:
            steps_data = json.loads(content)
            steps = [
                PlannedStep(
                    id=s.get("id", f"step_{i}"),
                    description=s.get("description", ""),
                    action=s.get("action", "llm_analysis"),
                    action_params=s.get("action_params", {}),
                    depends_on=s.get("depends_on", []),
                    expected_output=s.get("expected_output", ""),
                )
                for i, s in enumerate(steps_data)
            ]
            return Plan(steps=steps, original_query=query)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Plan parsing failed: %s, raw=%s", e, content[:200])
            # 返回一个单步计划：LLM 分析
            return Plan(
                steps=[PlannedStep(
                    id="step_1",
                    description=f"分析问题: {query}",
                    action="llm_analysis",
                    action_params={"query": query},
                    expected_output="分析结果",
                )],
                original_query=query,
            )

    async def _execute_step(self, step: PlannedStep, user_info: dict | None = None) -> str:
        """
        Executor：执行单个步骤。

        根据 action 类型执行不同的操作：
        - tool_call: 调用工具
        - llm_analysis: LLM 分析
        - skill: 调用技能（暂未实现）
        """
        if step.action == "tool_call":
            # 调用工具
            tool_name = step.action_params.get("tool", "")
            tool_args = step.action_params.get("args", {})
            return await self._call_tool(tool_name, tool_args, user_info)

        elif step.action == "llm_analysis":
            # LLM 分析
            prompt = f"""请分析以下任务步骤并给出结果。

步骤: {step.description}
期望输出: {step.expected_output}

请给出分析结果。"""
            return await self._call_llm(prompt)

        elif step.action == "skill":
            # 技能调用（预留）
            return "[Skill 调用暂未实现]"

        return "未知操作类型"

    async def _replan(self, current_plan: Plan, completed_step: PlannedStep, query: str) -> Plan:
        """
        Re-planner：每步完成后检查结果，判断是否需要调整计划。

        两种情况会触发重规划：
        1. 上一步失败了 → 调整后续步骤
        2. 上一步的结果和预期不一致 → LLM 判断是否需要调整
        """
        if completed_step.status == "failed":
            # 上一步失败，LLM 决定如何调整
            prompt = f"""原始任务: {query}

上一步 "{completed_step.description}" 执行失败:
{completed_step.result}

当前计划还有 {len(current_plan.steps) - current_plan.current_step - 1} 步未执行。
请判断是否需要调整后续计划。如果不需要调整，返回 empty。
如果需要调整，返回新的步骤数组（同规划格式）。

输出格式: {{"adjust": true/false, "reason": "...", "new_steps": [...]}}
new_steps 为空表示不需要调整。"""
            content = await self._call_llm(prompt)
            try:
                data = json.loads(content)
                if data.get("adjust") and data.get("new_steps"):
                    logger.info("Re-plan triggered: %s", data.get("reason", ""))
                    steps = [
                        PlannedStep(
                            id=s.get("id", f"replan_{i}"),
                            description=s.get("description", ""),
                            action=s.get("action", "llm_analysis"),
                            action_params=s.get("action_params", {}),
                            depends_on=s.get("depends_on", []),
                            expected_output=s.get("expected_output", ""),
                        )
                        for i, s in enumerate(data["new_steps"])
                    ]
                    # 替换未执行的步骤
                    remaining = current_plan.current_step + 1
                    current_plan.steps = current_plan.steps[:remaining] + steps
            except json.JSONDecodeError:
                pass

        return current_plan

    # ═══════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════

    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM 并返回响应文本。"""
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=30,
            trust_env=False,
        ) as client:
            resp = await client.post("/chat/completions", json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": "你是一个企业 IT 运维助手。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 1024,
            })
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            # 尝试提取 JSON（如果 LLM 返回了 markdown 格式）
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            return content

    async def _call_tool(self, tool_name: str, args: dict, user_info: dict | None = None) -> str:
        """调用工具并返回结果文本。"""
        for tool in self.tools:
            if tool.name == tool_name:
                try:
                    result = await tool.execute(**args)
                    if result.success:
                        return json.dumps(result.data, ensure_ascii=False)
                    else:
                        return f"工具执行失败: {result.error}"
                except Exception as e:
                    return f"工具执行异常: {e}"
        return f"工具 '{tool_name}' 未找到"

    def _deps_met(self, step: PlannedStep, all_steps: list[PlannedStep]) -> bool:
        """检查步骤的所有依赖是否已完成。"""
        if not step.depends_on:
            return True
        step_map = {s.id: s for s in all_steps}
        for dep_id in step.depends_on:
            dep = step_map.get(dep_id)
            if dep is None or dep.status != "success":
                return False
        return True


# ═══════════════════════════════════════════════
# HITL — Human Approval Gateway（人工审批网关）
# ═══════════════════════════════════════════════

@dataclass
class ApprovalTask:
    """
    审批任务数据结构。

    创建审批 → 等待审批 → 获取结果。
    """
    task_id: str
    tool_name: str
    tool_args: dict[str, Any]
    risk: Literal["low", "medium", "high"]
    session_id: str
    status: Literal["pending", "approved", "rejected", "timeout"] = "pending"
    reason: str = ""  # 审批人填写的理由
    created_at: float = 0.0
    timeout: int = 300  # 超时秒数


class HumanApprovalGateway:
    """
    人工审批网关 — 执行前的拦截层。

    什么时候触发审批？
    1. 工具标记为写操作（Tool.is_write = True）
    2. Classifier 判定风险为 high
    3. Guardrails 要求审批

    流程：
    1. 创建审批任务 → 记录到内存/Redis
    2. 暂停 Agent 执行 → 等待审批结果
    3. 审批通过 → 继续执行；驳回/超时 → 取消操作

    API 端点（在 agent.py 中注册）：
    - GET  /hitl/pending   → 查看待审批列表
    - POST /hitl/approve   → 审批通过
    - POST /hitl/reject    → 审批驳回
    """

    def __init__(self):
        self._tasks: dict[str, ApprovalTask] = {}
        self._timeout = settings.hitl.default_timeout

    async def request_approval(
        self,
        tool_name: str,
        tool_args: dict,
        risk: str,
        session_id: str,
    ) -> ApprovalTask:
        """
        发起审批请求。

        创建审批任务并记录，然后等待审批结果。
        超时未审批 → 自动驳回。
        """
        task = ApprovalTask(
            task_id=f"hitl_{session_id}_{tool_name}_{int(time.time())}",
            tool_name=tool_name,
            tool_args=tool_args,
            risk=risk,  # type: ignore
            session_id=session_id,
            created_at=time.time(),
            timeout=self._timeout,
        )
        self._tasks[task.task_id] = task
        logger.info(
            "HITL request created: tool=%s args=%s risk=%s task_id=%s",
            tool_name, tool_args, risk, task.task_id,
        )
        return task

    async def wait_for_result(self, task: ApprovalTask, poll_interval: float = 2.0) -> ApprovalTask:
        """
        等待审批结果（轮询模式）。

        Args:
            task: 审批任务
            poll_interval: 轮询间隔（秒）

        Returns:
            更新后的任务（status 变更为 approved/rejected/timeout）
        """
        deadline = time.time() + task.timeout
        while time.time() < deadline:
            current = self._tasks.get(task.task_id)
            if current and current.status in ("approved", "rejected"):
                return current
            await asyncio.sleep(poll_interval)

        # 超时 → 自动驳回
        task.status = "timeout"
        task.reason = "审批超时，自动驳回"
        logger.warning("HITL timeout: task_id=%s", task.task_id)
        return task

    async def approve(self, task_id: str, reason: str = "") -> bool:
        """审批通过。"""
        task = self._tasks.get(task_id)
        if task and task.status == "pending":
            task.status = "approved"
            task.reason = reason
            logger.info("HITL approved: task_id=%s reason=%s", task_id, reason)
            return True
        return False

    async def reject(self, task_id: str, reason: str = "") -> bool:
        """审批驳回。"""
        task = self._tasks.get(task_id)
        if task and task.status == "pending":
            task.status = "rejected"
            task.reason = reason
            logger.info("HITL rejected: task_id=%s reason=%s", task_id, reason)
            return True
        return False

    def get_pending(self) -> list[ApprovalTask]:
        """获取所有待审批任务。"""
        return [t for t in self._tasks.values() if t.status == "pending"]

    def get_by_session(self, session_id: str) -> list[ApprovalTask]:
        """获取某会话的所有审批任务。"""
        return [t for t in self._tasks.values() if t.session_id == session_id]


# 补上 asyncio 导入（wait_for_result 中使用了 asyncio.sleep）
import asyncio  # noqa: E402
