from __future__ import annotations

from super_agent.core.models import ToolResult
from super_agent.core.tool_registry import BaseTool


class EchoTool(BaseTool):
    name = "echo"
    description = "返回传入的参数，用于调试 Agent 工具调用"
    parameters = {"type": "object", "properties": {"message": {"type": "string"}}}
    is_write = False

    async def execute(self, message: str = "") -> ToolResult:
        return ToolResult(success=True, data={"echo": message})
