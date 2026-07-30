from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from super_agent.core.models import ToolResult


class BaseTool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict = {}
    is_write: bool = False
    timeout: int = 30

    @abstractmethod
    async def execute(self, **params: Any) -> ToolResult: ...


class ToolRegistry:
    """统一工具注册表。

    Cut 1: 关键词匹配
    Cut 2: 升级为语义匹配（Embedding 相似度）
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def match(self, query: str) -> list[BaseTool]:
        """关键词匹配：从 query 中查找 tool name 或 description 关键词。"""
        q = query.lower()
        matched: list[BaseTool] = []
        for tool in self._tools.values():
            if tool.name.lower() in q:
                matched.append(tool)
                continue
            for kw in tool.description.lower().split():
                if kw in q:
                    matched.append(tool)
                    break
        return matched

    async def execute(self, name: str, user_info: dict | None = None, **params: Any) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(success=False, error=f"Tool '{name}' not found")
        try:
            return await tool.execute(**params)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def list_all(self) -> list[BaseTool]:
        return list(self._tools.values())

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)
