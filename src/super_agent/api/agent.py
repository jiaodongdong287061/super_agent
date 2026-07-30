"""
Agent API — Phase 2 Agent 编排层的 HTTP 入口。

=== 全链路 ===
用户请求 → Guardrails（安全检测）→ Classifier（意图分类）
→ Runtime（执行引擎）→ [PlanExecute / ReAct / Knowledge] → 返回

=== 端点说明 ===
- POST /agent/chat        — Agent 对话（一次性返回）
- POST /agent/chat/stream — Agent 对话（流式 SSO 事件推送）
- POST /session/create    — 创建新会话
- POST /session/{id}/close — 关闭会话
- GET  /session/{id}      — 获取会话信息
- GET  /hitl/pending      — 待审批列表
- POST /hitl/approve      — 审批通过
- POST /hitl/reject       — 审批驳回
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from pydantic import BaseModel

from super_agent.knowledge.models import UserContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

# ── 全局 Runtime 实例（由 main.py 在启动时注入） ──
_runtime: Any = None          # AgentRuntime
_hitl: Any = None             # HumanApprovalGateway


def init_runtime(rt: Any, hitl: Any = None) -> None:
    """由 main.py 在启动时调用，注入 Runtime 实例。"""
    global _runtime, _hitl
    _runtime = rt
    _hitl = hitl


def get_runtime():
    """获取 Runtime 实例（确保已初始化）。"""
    assert _runtime is not None, "AgentRuntime not initialized"
    return _runtime


def get_hitl():
    """获取 HITL 实例。"""
    return _hitl


# ═══════════════════════════════════════════════
# 请求/响应模型
# ═══════════════════════════════════════════════

class AgentChatRequest(BaseModel):
    """Agent 对话请求体。"""
    query: str
    session_id: str = "default"
    user: UserContext = UserContext()


class AgentChatResponse(BaseModel):
    """Agent 对话响应体。"""
    answer: str
    session_id: str
    intent: str = ""
    risk: str = ""
    complexity: str = ""


# ═══════════════════════════════════════════════
# 用户解析（与 main.py 共享同一逻辑）
# ═══════════════════════════════════════════════

def _resolve_user(request: Request, body_user: UserContext) -> UserContext:
    """
    解析用户身份。

    优先级：
    1. SSO 开启且 JWT 有效 → request.state.user（SSO 认证用户）
    2. SSO 关闭或未认证 → 请求 body 中的 user 字段
    """
    state_user = getattr(request, "state", None)
    if state_user and hasattr(state_user, "user") and state_user.user.user_id:
        user = state_user.user
        logger.info(
            "Agent resolved user from SSO: user_id=%s department=%s",
            user.user_id, user.department,
        )
        return user
    logger.info(
        "Agent resolved user from body: user_id=%s department=%s",
        body_user.user_id, body_user.department,
    )
    return body_user


# ═══════════════════════════════════════════════
# 核心 API 端点
# ═══════════════════════════════════════════════

@router.post("/chat", response_model=AgentChatResponse)
async def agent_chat(req: AgentChatRequest, request: Request):
    """Agent 对话入口（一次性返回）。"""
    user = _resolve_user(request, req.user)
    logger.info("POST /agent/chat query=%s session=%s user_id=%s",
                req.query[:100], req.session_id, user.user_id)
    rt = get_runtime()

    answer = await rt.run(
        query=req.query,
        session_id=req.session_id,
        user_id=user.user_id,
        user_info={
            "user_id": user.user_id,
            "department": user.department,
            "tenant_id": user.tenant_id,
            "roles": user.roles,
            "permissions": user.permissions,
            "doc_level": user.doc_level,
        },
    )

    logger.info("POST /agent/chat done session=%s len(answer)=%d",
                req.session_id, len(answer))
    return AgentChatResponse(answer=answer, session_id=req.session_id)


@router.post("/chat/stream")
async def agent_chat_stream(req: AgentChatRequest, request: Request):
    """Agent 对话入口（流式 SSE）。"""
    user = _resolve_user(request, req.user)
    logger.info("POST /agent/chat/stream query=%s session=%s user_id=%s",
                req.query[:100], req.session_id, user.user_id)
    rt = get_runtime()

    async def _generate():
        async for event in rt.run_stream(
            query=req.query,
            session_id=req.session_id,
            user_id=user.user_id,
            user_info={
                "user_id": user.user_id,
                "department": user.department,
                "tenant_id": user.tenant_id,
                "roles": user.roles,
                "permissions": user.permissions,
                "doc_level": user.doc_level,
            },
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


# ═══════════════════════════════════════════════
# 会话管理端点
# ═══════════════════════════════════════════════

@router.post("/session/create")
async def create_session(mode: str = "default"):
    """创建新的 Agent 会话。"""
    import uuid
    session_id = f"session_{uuid.uuid4().hex[:12]}"
    logger.info("POST /agent/session/create session=%s mode=%s", session_id, mode)
    return {"session_id": session_id, "mode": mode}


@router.post("/session/{session_id}/close")
async def close_session(session_id: str):
    """关闭并归档会话。"""
    rt = get_runtime()
    logger.info("POST /agent/session/%s/close", session_id)
    state = await rt.state_manager.restore(session_id)
    if state:
        await rt.state_manager.archive(state)
        logger.info("Session closed: %s", session_id)
        return {"status": "closed", "session_id": session_id}
    logger.warning("Session not found: %s", session_id)
    return {"status": "not_found", "session_id": session_id}


@router.get("/session/{session_id}")
async def get_session(session_id: str):
    """获取会话信息。"""
    rt = get_runtime()
    logger.info("GET /agent/session/%s", session_id)
    state = await rt.state_manager.restore(session_id)
    if state:
        return {
            "session_id": state.session_id,
            "query": state.query[:100] if state.query else "",
            "intent": state.intent,
            "risk": state.risk,
            "complexity": state.complexity,
            "step_index": state.step_index,
            "tool_calls_count": len(state.tool_calls),
        }
    return {"error": "session_not_found"}


# ═══════════════════════════════════════════════
# 审批端点
# ═══════════════════════════════════════════════

class ApprovalRequest(BaseModel):
    task_id: str
    action: Literal["approve", "reject"]
    reason: str = ""


@router.get("/hitl/pending")
async def get_pending_approvals():
    """查看所有待审批任务。"""
    hitl = get_hitl()
    logger.info("GET /agent/hitl/pending")
    if not hitl:
        return {"tasks": []}
    tasks = hitl.get_pending()
    return {
        "tasks": [
            {
                "task_id": t.task_id,
                "tool_name": t.tool_name,
                "tool_args": t.tool_args,
                "risk": t.risk,
                "session_id": t.session_id,
                "created_at": t.created_at,
            }
            for t in tasks
        ]
    }


@router.post("/hitl/approve")
async def approve_action(req: ApprovalRequest):
    """审批通过。"""
    hitl = get_hitl()
    logger.info("POST /agent/hitl/approve task_id=%s reason=%s", req.task_id, req.reason)
    if not hitl:
        return {"status": "error", "message": "HITL not initialized"}
    success = await hitl.approve(req.task_id, req.reason)
    return {"status": "approved" if success else "failed"}


@router.post("/hitl/reject")
async def reject_action(req: ApprovalRequest):
    """审批驳回。"""
    hitl = get_hitl()
    logger.info("POST /agent/hitl/reject task_id=%s reason=%s", req.task_id, req.reason)
    if not hitl:
        return {"status": "error", "message": "HITL not initialized"}
    success = await hitl.reject(req.task_id, req.reason)
    return {"status": "rejected" if success else "failed"}
