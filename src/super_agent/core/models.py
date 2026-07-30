from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ToolCallRecord:
    name: str
    params: dict[str, Any]
    result: Any = None
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str | None = None


@dataclass
class AgentState:
    user_id: str
    session_id: str
    query: str
    intent: Literal["qa", "knowledge", "action"] = "action"
    risk: Literal["low", "medium", "high"] = "low"
    complexity: Literal["simple", "multi_step"] = "simple"
    messages: list[dict] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    observations: list[Any] = field(default_factory=list)
    approval_status: Literal["none", "pending", "approved", "rejected"] = "none"
    max_steps: int = 10
    started_at: float = 0.0
    finished_at: float | None = None
    execution_result: str | None = None
    user_info: dict = field(default_factory=dict)
