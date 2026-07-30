"""
StateManager — Agent 执行状态的读写入口。

=== 三层存储架构 ===
Agent Runtime → AgentState（内存） → Redis（会话快照） → MySQL（归档）
                     ↑                      ↑
               ReAct 循环操作          异步持久化

=== 数据流 ===
1. Runtime 创建 State（create）→ 写入内存 + Redis
2. 每步 ReAct 循环 → save_snapshot → 异步刷 Redis（Hash + Stream）
3. 服务重启 → restore → 从 Redis 恢复
4. 会话结束 → archive → 写入 MySQL，清理 Redis

=== Redis 存储结构 ===
Hash key: agent:state:{session_id}
  - 结构化字段：user_id, intent, task, step_index, approval_status...
  - TTL: 30 分钟，每次活跃刷新

Stream key: agent:state:{session_id}:messages
  - 对话历史（message 数组），量大不适合放 Hash
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from typing import Any

from super_agent.config import settings
from super_agent.core.models import AgentState

logger = logging.getLogger(__name__)


class StateManager:
    """
    Agent State 管理器 — 三层持久化。

    当前实现：
    - 内存模式：会话数据存本地字典（开发/测试用）
    - Redis 模式：通过 Redis Hash + Stream 持久化

    后续实现：
    - MySQL 模式：会话归档到 MySQL agent_sessions 表
    """

    def __init__(self, redis_client=None, db_client=None):
        """
        Args:
            redis_client: Redis 异步客户端（redis.asyncio.Redis）
            db_client: MySQL 异步数据库连接
        """
        self._sessions: dict[str, AgentState] = {}
        self._redis = redis_client
        self._db = db_client
        self._redis_ttl = settings.state_config.redis_ttl
        self._max_tool_records = settings.state_config.max_tool_records

    # ═══════════════════════════════════════════════
    # 生命周期：创建 → 快照 → 恢复 → 归档
    # ═══════════════════════════════════════════════

    async def create(self, session_id: str, query: str, user_id: str) -> AgentState:
        """
        创建新的 Agent 会话状态。

        在 Runtime 启动时调用，初始化 State。
        """
        state = AgentState(
            user_id=user_id,
            session_id=session_id,
            query=query,
            started_at=time.time(),
            max_steps=settings.runtime.max_steps,
        )
        # 内存模式
        self._sessions[session_id] = state

        # Redis 模式
        if self._redis:
            await self._save_redis_snapshot(state)

        logger.debug("State created: session=%s query=%s", session_id, query[:60])
        return state

    async def save_snapshot(self, state: AgentState) -> None:
        """
        每步 ReAct 循环结束后调用，保存状态快照。

        设计要点：
        - messages 单独存到 Redis Stream（避免 Hash 读写大对象）
        - tool_calls 只保留最近 N 条（全量在 MySQL 归档时写入）
        - 异步 fire-and-forget，不阻塞主循环

        Args:
            state: 当前的 AgentState
        """
        if not self._redis:
            return  # 内存模式：无操作

        key = f"agent:state:{state.session_id}"

        # 写入结构化字段到 Redis Hash
        tool_calls_trimmed = state.tool_calls[-self._max_tool_records:]
        await self._redis.hset(key, mapping={
            "user_id": state.user_id,
            "intent": state.intent,
            "risk": state.risk,
            "complexity": state.complexity,
            "task": state.task or "",
            "step_index": state.step_index,
            "approval_status": state.approval_status,
            "tool_calls": json.dumps(
                [asdict(t) if hasattr(t, "__dataclass_fields__") else t for t in tool_calls_trimmed],
                default=str,
            ),
        })
        await self._redis.expire(key, self._redis_ttl)

        # messages 写入到单独的 Redis key（JSON 序列化整个数组）
        if state.messages:
            msg_key = f"agent:state:{state.session_id}:messages"
            await self._redis.set(
                msg_key,
                json.dumps(state.messages, default=str),
                ex=self._redis_ttl,
            )

    async def restore(self, session_id: str) -> AgentState | None:
        """
        服务重启后恢复会话状态。

        从 Redis 重建结构化字段 + 从 Stream 恢复 messages。
        如果 Redis 中没有数据，尝试从内存恢复。

        Args:
            session_id: 会话 ID

        Returns:
            AgentState | None: 恢复的 state，不存在则返回 None
        """
        # 优先从内存恢复
        if session_id in self._sessions:
            return self._sessions[session_id]

        # 然后从 Redis 恢复
        if self._redis:
            key = f"agent:state:{session_id}"
            data = await self._redis.hgetall(key)
            if data:
                state = AgentState(
                    user_id=data.get(b"user_id", b"").decode(),
                    session_id=session_id,
                    query=data.get(b"query", b"").decode(),
                    intent=data.get(b"intent", b"action").decode(),  # type: ignore
                    risk=data.get(b"risk", b"low").decode(),  # type: ignore
                    complexity=data.get(b"complexity", b"simple").decode(),  # type: ignore
                    task=data.get(b"task", b"").decode(),
                    step_index=int(data.get(b"step_index", 0)),
                    approval_status=data.get(b"approval_status", b"none").decode(),  # type: ignore
                    started_at=float(data.get(b"started_at", time.time())),
                    max_steps=settings.runtime.max_steps,
                )
                # 从 Redis 恢复 messages
                msg_key = f"agent:state:{session_id}:messages"
                msg_raw = await self._redis.get(msg_key)
                if msg_raw:
                    if isinstance(msg_raw, bytes):
                        msg_raw = msg_raw.decode()
                    try:
                        state.messages = json.loads(msg_raw)
                    except (json.JSONDecodeError, TypeError):
                        pass

                self._sessions[session_id] = state
                return state

        return None

    async def archive(self, state: AgentState) -> None:
        """
        会话完成/超时/失败后归档到 MySQL。

        Part C 的 OpenTelemetry 追踪直接读此表数据，
        不做二次埋点。

        Args:
            state: 已完成的 AgentState
        """
        state.finished_at = time.time()

        # 更新内存状态
        self._sessions[state.session_id] = state

        # 写入 MySQL 归档
        if self._db:
            try:
                await self._db.execute("""
                    INSERT INTO agent_sessions
                    (session_id, user_id, query, intent, task, status,
                     step_index, approval_status,
                     tool_calls, messages, execution_result,
                     started_at, finished_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())
                """,
                    state.session_id, state.user_id, state.query, state.intent,
                    state.task or "", "completed",
                    state.step_index, state.approval_status,
                    json.dumps([
                        asdict(t) if hasattr(t, "__dataclass_fields__") else t
                        for t in state.tool_calls
                    ], default=str),
                    json.dumps(state.messages, default=str),
                    state.execution_result, state.started_at,
                )
            except Exception as e:
                logger.warning("State archive to MySQL failed: %s", e)

        # 清理 Redis
        if self._redis:
            try:
                keys = [
                    f"agent:state:{state.session_id}",
                    f"agent:state:{state.session_id}:messages",
                ]
                for k in keys:
                    await self._redis.delete(k)
            except Exception as e:
                logger.warning("State Redis cleanup failed: %s", e)

        logger.info(
            "State archived: session=%s steps=%d tool_calls=%d",
            state.session_id, state.step_index, len(state.tool_calls),
        )

    # ═══════════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════════

    async def _save_redis_snapshot(self, state: AgentState) -> None:
        """将状态完整写入 Redis（用于 create 和 restore 后的首次保存）。"""
        if not self._redis:
            return
        key = f"agent:state:{state.session_id}"
        mapping = {
            "user_id": state.user_id,
            "query": state.query,
            "intent": state.intent,
            "risk": state.risk,
            "complexity": state.complexity,
            "task": state.task or "",
            "step_index": state.step_index,
            "approval_status": state.approval_status,
            "started_at": state.started_at,
            "max_steps": state.max_steps,
        }
        await self._redis.hset(key, mapping={k: str(v) for k, v in mapping.items()})
        await self._redis.expire(key, self._redis_ttl)

    async def cleanup_session(self, session_id: str) -> None:
        """清理会话数据（内存 + Redis）。"""
        self._sessions.pop(session_id, None)
        if self._redis:
            keys = [
                f"agent:state:{session_id}",
                f"agent:state:{session_id}:messages",
            ]
            for k in keys:
                await self._redis.delete(k)
