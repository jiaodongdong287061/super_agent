"""
AgentRuntime — 单 Agent 执行引擎。

=== 核心思想 ===
Runtime 是一个"空壳引擎"——它不预设任何专业知识。
它会什么，取决于运行时加载了什么工具、知识库和技能。

=== 执行路径（四条） ===
根据 Classifier 的输出，走不同路径：

                    ┌── qa ────────→ 直接 LLM 回答（不查库不调工具）
                    │
Classifier 输出 ─────┼── knowledge ─→ RAG 检索 → 注入 context → LLM 回答
                    │                 (无结果 → LLM 兜底)
                    │
                    └── action ───────┼── simple → 加载工具 → ReAct 循环 → 回答
                                    │
                                    └── multi_step → 加载 Skills + RAG + 工具
                                                      → PlanExecute → 回答

=== ReAct 循环 ===
核心就是三步：
1. Think（思考）:  LLM 观察当前状态，决定下一步
2. Act（行动）:    执行工具调用
3. Observe（观察）: 收集结果，喂回给 LLM
→ 循环直到 LLM 决定输出答案
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from super_agent.config import settings
from super_agent.core.classifier import (
    ClassificationResult,
    HybridClassifier,
    RuleClassifier,
)
from super_agent.core.guardrails import Guardrails
from super_agent.core.models import AgentState, ToolCallRecord
from super_agent.core.plan_execute import HumanApprovalGateway, PlanExecute
from super_agent.core.state import StateManager
from super_agent.core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# 系统提示词模板 — 告诉 LLM 它有什么工具可用
_SYSTEM_PROMPT = """你是一个智能助手，可以根据用户的问题选择合适的工具来帮助解决问题。

你需要判断用户的问题是否需要调用工具。如果需要，请使用合适的工具并传入正确的参数。
如果不需要工具，直接回答用户的问题即可。

可用的工具：
{tools_desc}

注意：
1. 只有明确需要操作时才调用工具
2. 调用工具时请严格按照工具参数格式
3. 根据工具返回的结果组织最终答案"""


class AgentRuntime:
    """
    Agent 运行时 — 整个架构的核心执行引擎。

    用法：
        runtime = AgentRuntime(tool_registry, classifier, state_manager, guardrails)

    RAG 检索：
        直接使用 knowledge.retrieval_pipeline 共享模块（与 main.py 同一份代码）。
        _load_rag() 内部调用 build_retriever + retrieve_chunks + chunks_to_dicts。

    架构位置：
        agent.py (API 层) → runtime.py (执行引擎) → tools / rag / skills
    """

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        classifier: HybridClassifier | RuleClassifier | None = None,
        state_manager: StateManager | None = None,
        guardrails: Guardrails | None = None,
        hitl: HumanApprovalGateway | None = None,
    ):
        self.tool_registry = tool_registry or ToolRegistry()
        self.classifier = classifier or HybridClassifier()
        self.state_manager = state_manager or StateManager()
        self.guardrails = guardrails or Guardrails()
        self.hitl = hitl or HumanApprovalGateway()

        # ── LLM 调用配置（从 config 读取） ──
        cfg = settings.llm
        self._base_url = cfg.oneapi_base_url.rstrip("/")
        self._api_key = cfg.oneapi_api_key
        self._model = cfg.default_model
        self._timeout = cfg.request_timeout
        self._default_temperature = cfg.default_temperature
        self._default_max_tokens = cfg.max_tokens

        # ── 运行时配置 ──
        self._max_steps = settings.runtime.max_steps
        self._max_tools = settings.runtime.max_tools
        self._enable_hitl = settings.runtime.enable_hitl

        # HTTP 客户端（懒加载）
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """HTTP 客户端，首次访问时创建（懒加载模式）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
                trust_env=False,
            )
        return self._client

    # ═══════════════════════════════════════════════
    # 公开接口
    # ═══════════════════════════════════════════════

    async def run(
        self,
        query: str,
        session_id: str,
        user_id: str,
        user_info: dict | None = None,
    ) -> str:
        """
        同步模式：完整执行一次 Agent 调用，返回最终回答。

        流程：
        Guardrails → Classifier → Runtime(四条路径之一) → 返回答案
        """
        state = await self.state_manager.create(session_id, query, user_id)
        if user_info:
            state.user_info = user_info

        # ── 第 1 步：Guardrails 输入检测 ──
        perms = (user_info or {}).get("permissions", [])
        gr = self.guardrails.check_input(query, permissions=perms, user_info=user_info)
        if gr.verdict == "block":
            state.execution_result = gr.reason
            return gr.reason

        # ── 第 2 步：Classifier 意图识别 ──
        # 异步调用 HybridClassifier（规则 + LLM 兜底）
        classification = await self.classifier.classify(query)
        state.intent = classification.intent
        state.risk = classification.risk
        state.complexity = classification.complexity
        logger.info(
            "Classification: intent=%s risk=%s complexity=%s source=%s",
            classification.intent, classification.risk,
            classification.complexity, classification.source,
        )

        # ── 第 3-4 步：按路径执行 ──
        return await self._execute_by_path(state, classification)

    async def run_stream(
        self,
        query: str,
        session_id: str,
        user_id: str,
        user_info: dict | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        流式模式：逐步产出 SSE 事件，前端可以实现打字机效果。

        产出的事件类型：
        - type=start:      会话开始
        - type=guardrails: Guardrails 检测结果
        - type=intent:     Classifier 判定的意图
        - type=token:      LLM 生成的文本片段
        - type=tool_call:  工具调用
        - type=tool_result: 工具返回结果
        - type=step:       PlanExecute 步骤信息
        - type=done:       回答完成
        """
        state = await self.state_manager.create(session_id, query, user_id)
        if user_info:
            state.user_info = user_info

        yield {"type": "start", "session_id": session_id}

        # Guardrails
        perms = (user_info or {}).get("permissions", [])
        gr = self.guardrails.check_input(query, permissions=perms, user_info=user_info)
        yield {"type": "guardrails", "verdict": gr.verdict, "reason": gr.reason}
        if gr.verdict == "block":
            state.execution_result = gr.reason
            yield {"type": "token", "text": gr.reason}
            yield {"type": "done", "answer": gr.reason}
            return

        # Classifier
        classification = await self.classifier.classify(query)
        state.intent = classification.intent
        state.risk = classification.risk
        state.complexity = classification.complexity
        yield {
            "type": "intent",
            "intent": classification.intent,
            "risk": classification.risk,
            "complexity": classification.complexity,
        }

        # 按路径执行（流式）
        async for event in self._execute_by_path_stream(state, classification):
            yield event

        yield {"type": "done", "answer": state.execution_result or ""}

    # ═══════════════════════════════════════════════
    # 执行路径分发
    # ═══════════════════════════════════════════════

    async def _execute_by_path(self, state: AgentState, classification: ClassificationResult) -> str:
        """
        根据 Classifier 的输出分发到四条执行路径。

        路径 1: qa → 直接 LLM 回答（最快）
        路径 2: knowledge → RAG 检索 → LLM 回答（有结果用企业知识，无结果 LLM 兜底）
        路径 3: action + simple → 加载工具 → ReAct 循环
        路径 4: action + multi_step → 加载工具 → PlanExecute 管线
        """
        if classification.intent == "qa":
            # 路径 1：纯聊天，不查库不调工具
            return await self._run_llm_direct(state)

        elif classification.intent == "knowledge":
            # 路径 2：知识问答，先查 RAG
            return await self._run_knowledge(state, classification)

        elif classification.intent == "action":
            # 路径 3/4：工具调用
            tools = self._load_tools(state.query)

            if classification.complexity == "multi_step" and settings.runtime.enable_plan:
                # 路径 4：复杂任务 → PlanExecute
                return await self._run_with_plan(state, tools, classification)
            else:
                # 路径 3：简单任务 → ReAct 循环
                return await self._run_react(state, tools, classification)

        # 兜底
        return await self._run_llm_direct(state)

    async def _execute_by_path_stream(self, state: AgentState, classification: ClassificationResult) -> AsyncGenerator[dict, None]:
        """流式版本的四条路径分发。"""
        if classification.intent == "qa":
            async for event in self._run_llm_direct_stream(state):
                yield event

        elif classification.intent == "knowledge":
            async for event in self._run_knowledge_stream(state, classification):
                yield event

        elif classification.intent == "action":
            tools = self._load_tools(state.query)

            if classification.complexity == "multi_step" and settings.runtime.enable_plan:
                async for event in self._run_with_plan_stream(state, tools, classification):
                    yield event
            else:
                async for event in self._run_react_stream(state, tools, classification):
                    yield event

    # ═══════════════════════════════════════════════
    # 路径 1：纯 LLM 回答（qa）
    # ═══════════════════════════════════════════════

    async def _run_llm_direct(self, state: AgentState) -> str:
        """
        路径 1（qa）：直接问 LLM，不做检索不调工具。

        适用场景：问候、闲聊、Agent 能力咨询。
        """
        messages = [
            {"role": "system", "content": "你是一个知识助手，请根据用户问题直接回答。"},
            {"role": "user", "content": state.query},
        ]
        body = {
            "model": self._model,
            "messages": messages,
            "temperature": self._default_temperature,
            "max_tokens": self._default_max_tokens,
        }
        resp = await self.client.post("/chat/completions", json=body)
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        state.execution_result = content
        return content

    async def _run_llm_direct_stream(self, state: AgentState) -> AsyncGenerator[dict, None]:
        """路径 1 的流式版本。"""
        messages = [
            {"role": "system", "content": "你是一个知识助手，请根据用户问题直接回答。"},
            {"role": "user", "content": state.query},
        ]
        body = {
            "model": self._model,
            "messages": messages,
            "temperature": self._default_temperature,
            "max_tokens": self._default_max_tokens,
            "stream": True,
        }
        full_content = ""
        async with self.client.stream("POST", "/chat/completions", json=body) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    choices = chunk.get("choices") or [{}]
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_content += content
                        yield {"type": "token", "text": content}
                except json.JSONDecodeError:
                    continue
        state.execution_result = full_content

    # ═══════════════════════════════════════════════
    # 路径 2：知识问答（knowledge）
    # ═══════════════════════════════════════════════

    async def _run_knowledge(self, state: AgentState, classification: ClassificationResult) -> str:
        """
        路径 2（knowledge）：先查企业知识库，再结合上下文回答。

        策略：
        - 有检索结果 → 注入 context，LLM 基于企业知识回答
        - 无检索结果 → LLM 用自己的知识兜底（不报错）
        """
        rag_context = await self._load_rag(state.query, state.user_info)
        if rag_context:
            messages = self._build_rag_messages(state.query, rag_context)
        else:
            # RAG 无结果：LLM 用自己的知识兜底，但需注明非企业知识库内容
            messages = [
                {"role": "system", "content": "你是一个知识助手。注意：企业知识库中未找到与问题相关的文档，以下回答基于 AI 通用知识，非企业知识库内容，仅供参考。"},
                {"role": "user", "content": state.query},
            ]

        body = {
            "model": self._model,
            "messages": messages,
            "temperature": self._default_temperature,
            "max_tokens": self._default_max_tokens,
        }
        resp = await self.client.post("/chat/completions", json=body)
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        state.execution_result = content
        return content

    async def _run_knowledge_stream(self, state: AgentState, classification: ClassificationResult) -> AsyncGenerator[dict, None]:
        """路径 2 的流式版本。"""
        rag_context = await self._load_rag(state.query, state.user_info)
        if rag_context:
            # yield 来源信息
            sources_data = [
                {
                    "chunk_id": c["id"],
                    "source_doc": c["source_doc"],
                    "page_numbers": c.get("page_numbers"),
                    "content_snippet": c.get("content", "")[:200],
                }
                for c in rag_context
            ]
            yield {"type": "sources", "sources": sources_data}
            messages = self._build_rag_messages(state.query, rag_context)
        else:
            # RAG 无结果：LLM 用自己的知识兜底，前端标记黄色
            yield {"type": "source", "source": "llm_fallback"}
            messages = [
                {"role": "system", "content": "你是一个知识助手。注意：企业知识库中未找到与问题相关的文档，以下回答基于 AI 通用知识，非企业知识库内容，仅供参考。"},
                {"role": "user", "content": state.query},
            ]

        body = {
            "model": self._model,
            "messages": messages,
            "temperature": self._default_temperature,
            "max_tokens": self._default_max_tokens,
            "stream": True,
        }
        full_content = ""
        async with self.client.stream("POST", "/chat/completions", json=body) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    choices = chunk.get("choices") or [{}]
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_content += content
                        yield {"type": "token", "text": content}
                except json.JSONDecodeError:
                    continue
        state.execution_result = full_content

    # ═══════════════════════════════════════════════
    # 路径 3：ReAct 循环（action + simple）
    # ═══════════════════════════════════════════════

    async def _run_react(
        self,
        state: AgentState,
        tools: list,
        classification: ClassificationResult,
    ) -> str:
        """
        路径 3（ReAct）：LLM 决定是否调工具 → 执行 → 继续或返回。

        三步循环：
        1. LLM 思考：需要调工具吗？调哪个？
        2. Runtime 执行：调工具、拿结果
        3. 结果喂回 LLM → 继续循环或输出答案
        """
        tools_desc = self._format_tools_desc(tools)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT.format(tools_desc=tools_desc)},
            {"role": "user", "content": state.query},
        ]

        for step in range(self._max_steps):
            body = {
                "model": self._model,
                "messages": messages,
                "temperature": self._default_temperature,
                "max_tokens": self._default_max_tokens,
            }
            resp = await self.client.post("/chat/completions", json=body)
            data = resp.json()
            msg = data["choices"][0]["message"]
            content = msg.get("content", "")

            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                # LLM 决定直接回答，不再调工具
                state.execution_result = content
                return content

            # LLM 要求调工具 → 执行
            for tc in tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                try:
                    params = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    params = {}

                # HITL 检查：高风险写操作需要人工审批
                if self._needs_approval(name, classification):
                    approved = await self._request_approval(name, params, state)
                    if not approved:
                        state.tool_calls.append(ToolCallRecord(
                            name=name, params=params,
                            result=None, error="操作被驳回",
                            duration_ms=0,
                        ))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": f"工具 {name} 的操作被审批驳回，跳过执行。",
                        })
                        continue

                # 执行工具
                t0 = time.time()
                result = await self.tool_registry.execute(name, **params)
                elapsed = (time.time() - t0) * 1000

                # 记录工具调用
                state.tool_calls.append(ToolCallRecord(
                    name=name, params=params,
                    result=result.data if result.success else None,
                    error=result.error if not result.success else None,
                    duration_ms=elapsed,
                ))

                # 把工具结果喂回给 LLM
                messages.append({"role": "assistant", "content": content, "tool_calls": [tc]})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(
                        result.data if result.success else {"error": result.error},
                        ensure_ascii=False,
                    ),
                })

            # 每步结束保存快照
            await self.state_manager.save_snapshot(state)

        return "操作步骤过多，请简化后重试"

    async def _run_react_stream(
        self,
        state: AgentState,
        tools: list,
        classification: ClassificationResult,
    ) -> AsyncGenerator[dict, None]:
        """路径 3 的流式版本。"""
        tools_desc = self._format_tools_desc(tools)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT.format(tools_desc=tools_desc)},
            {"role": "user", "content": state.query},
        ]

        for step in range(self._max_steps):
            yield {"type": "step", "step": step + 1, "max_steps": self._max_steps}

            body = {
                "model": self._model,
                "messages": messages,
                "temperature": self._default_temperature,
                "max_tokens": self._default_max_tokens,
            }
            resp = await self.client.post("/chat/completions", json=body)
            data = resp.json()
            msg = data["choices"][0]["message"]
            content = msg.get("content", "")

            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                # 无工具调用 → 流式输出最终答案
                body["stream"] = True
                full = ""
                async with self.client.stream("POST", "/chat/completions", json=body) as sr:
                    async for line in sr.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                            t = delta.get("content", "")
                            if t:
                                full += t
                                yield {"type": "token", "text": t}
                        except json.JSONDecodeError:
                            continue
                state.execution_result = full
                return

            for tc in tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                try:
                    params = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    params = {}

                yield {"type": "tool_call", "name": name, "params": params}

                if self._needs_approval(name, classification):
                    approved = await self._request_approval(name, params, state)
                    if not approved:
                        yield {"type": "tool_result", "name": name, "success": False, "error": "审批驳回"}
                        continue

                t0 = time.time()
                result = await self.tool_registry.execute(name, **params)
                elapsed = (time.time() - t0) * 1000

                state.tool_calls.append(ToolCallRecord(
                    name=name, params=params,
                    result=result.data if result.success else None,
                    error=result.error if not result.success else None,
                    duration_ms=elapsed,
                ))

                yield {
                    "type": "tool_result", "name": name,
                    "success": result.success, "data": result.data, "error": result.error,
                }

                messages.append({"role": "assistant", "content": content, "tool_calls": [tc]})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(
                        result.data if result.success else {"error": result.error},
                        ensure_ascii=False,
                    ),
                })

            await self.state_manager.save_snapshot(state)

        state.execution_result = "操作步骤过多，请简化后重试"
        yield {"type": "token", "text": state.execution_result}

    # ═══════════════════════════════════════════════
    # 路径 4：PlanExecute（action + multi_step）
    # ═══════════════════════════════════════════════

    async def _run_with_plan(
        self,
        state: AgentState,
        tools: list,
        classification: ClassificationResult,
    ) -> str:
        """
        路径 4（PlanExecute）：复杂任务的管线式执行。

        三步流程：
        1. Planner — LLM 拆解任务为有序步骤
        2. Executor — 逐步执行（工具/LLM 分析）
        3. Re-planner — 每步完成后检查，必要时调整后续计划
        """
        planner = PlanExecute(tools=tools, llm_client=self.client)
        result = await planner.execute(
            query=state.query,
            user_info=state.user_info,
        )
        state.execution_result = result.summary
        return result.summary

    async def _run_with_plan_stream(
        self,
        state: AgentState,
        tools: list,
        classification: ClassificationResult,
    ) -> AsyncGenerator[dict, None]:
        """路径 4 的流式版本。"""
        planner = PlanExecute(tools=tools, llm_client=self.client)
        async for event in planner.execute_stream(query=state.query, user_info=state.user_info):
            yield event
            if event.get("type") == "done":
                state.execution_result = event.get("summary", "")

    # ═══════════════════════════════════════════════
    # 能力加载
    # ═══════════════════════════════════════════════

    def _load_tools(self, query: str) -> list:
        """
        根据 query 匹配相关工具。

        设计原则：只加载当前任务需要的工具，不全量加载。
        全量加载会导致：
        1. LLM 的 context 被无关工具占满
        2. LLM 在几十个工具中选错
        3. Token 消耗暴增
        """
        all_tools = self.tool_registry.list_all()
        matched = self.tool_registry.match(query)
        if len(matched) > self._max_tools:
            logger.warning("Too many tools matched (%d), limiting to %d", len(matched), self._max_tools)
            matched = matched[:self._max_tools]
        return matched

    async def _load_rag(self, query: str, user_info: dict | None = None, top_k: int = 5) -> list | None:
        """
        执行 RAG 检索全流程（直接使用 knowledge.retrieval_pipeline 共享模块）。

        完整管线：
        query → QueryProcessor(改写+扩展) → 向量检索(3×top_k) → BM25(可选)
            → RRF融合 → 去重 → chunks_to_dicts → 返回

        Args:
            query: 用户问题
            user_info: 用户上下文（部门、权限等），用于构建租户级检索器
            top_k: 返回 top_k 条结果

        Returns:
            list[dict] | None: chunks dict 列表，每项有 id/content/source_doc/page_numbers
        """
        from super_agent.knowledge.models import UserContext
        from super_agent.knowledge.retrieval_pipeline import build_retriever, retrieve_chunks, chunks_to_dicts

        # 从 user_info dict 重建 UserContext（与 main.py 中 _resolve_user 的逻辑一致）
        ctx = UserContext(
            user_id=(user_info or {}).get("user_id", ""),
            department=(user_info or {}).get("department", ""),
            tenant_id=(user_info or {}).get("tenant_id", ""),
            roles=(user_info or {}).get("roles", []),
            doc_level=(user_info or {}).get("doc_level", "L1"),
        )
        try:
            # build_retriever / retrieve_chunks 是同步函数（内部有同步 I/O 操作），
            # 用 asyncio.to_thread 扔到线程池执行，不阻塞事件循环
            retriever = await asyncio.to_thread(build_retriever, ctx)
            chunks = await asyncio.to_thread(
                retrieve_chunks, query, top_k, retriever, None, ctx,
            )
            dicts = chunks_to_dicts(chunks)
            if dicts:
                logger.info("RAG retrieved %d chunks for query: %s", len(dicts), query[:60])
                for i, d in enumerate(dicts[:3]):
                    logger.info("RAG chunk[%d]: source=%s content_preview=%s", i, d.get("source_doc",""), d.get("content","")[:200])
            else:
                logger.info("RAG returned 0 chunks for query: %s", query[:60])
            return dicts
        except Exception as e:
            logger.warning("RAG retrieval failed, fallback to LLM only: %s", e)
            return None

    def _build_rag_messages(self, query: str, rag_context: list) -> list[dict]:
        """构建包含 RAG 上下文的 LLM 消息。"""
        context_text = "\n\n".join(
            f"[来源: {c.get('source_doc', c.get('id', 'unknown'))}]\n{c.get('content', str(c))}"
            for c in rag_context
        )
        system_prompt = f"""你是一个企业知识助手。请基于以下企业知识库内容回答用户问题。

如果知识库内容不足以回答，请基于你自己的知识补充回答，不要说自己"无法回答"。

企业知识库内容：
{context_text}"""
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ]

    # ═══════════════════════════════════════════════
    # HITL 审批
    # ═══════════════════════════════════════════════

    def _needs_approval(self, tool_name: str, classification: ClassificationResult) -> bool:
        """
        判断是否需要人工审批。

        触发条件：
        1. 工具标记为写操作
        2. 风险等级达到阈值（默认 high）
        3. HITL 功能已启用
        """
        if not self._enable_hitl:
            return False
        if classification.risk == "high":
            return True
        # 检查工具是否为写操作
        tool = self.tool_registry.get(tool_name)
        if tool and tool.is_write:
            return True
        return False

    async def _request_approval(self, tool_name: str, params: dict, state: AgentState) -> bool:
        """
        发起审批请求并等待结果。

        返回 True 表示审批通过，False 表示驳回/超时。
        """
        task = await self.hitl.request_approval(
            tool_name=tool_name,
            tool_args=params,
            risk=state.risk,
            session_id=state.session_id,
        )
        result = await self.hitl.wait_for_result(task)
        return result.status == "approved"

    # ═══════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════

    def _format_tools_desc(self, tools: list) -> str:
        """把工具列表格式化为 LLM 可读的描述文本。"""
        return "\n".join(
            f"- {t.name}: {t.description} (参数: {json.dumps(t.parameters, ensure_ascii=False)})"
            for t in tools
        )

    async def close(self):
        """关闭 HTTP 客户端，释放资源。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
