"""工具注册表：管理 Agent 可调用的工具，支持 OpenAI function calling 格式。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(slots=True)
class Tool:
    """工具定义：名称、描述、参数 schema、异步处理函数。

    parameters: JSON Schema 格式的参数定义，用于 OpenAI function calling API。
    """
    name: str
    description: str
    handler: ToolHandler
    parameters: dict[str, Any] | None = None


class ToolsRegistry:
    """工具注册表：注册、查找、列举、执行工具。不允许重复注册同名工具。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """生成 OpenAI function calling 格式的工具定义列表。"""
        result: list[dict[str, Any]] = []
        for tool in self._tools.values():
            definition: dict[str, Any] = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                },
            }
            if tool.parameters:
                definition["function"]["parameters"] = tool.parameters
            else:
                definition["function"]["parameters"] = {
                    "type": "object",
                    "properties": {},
                }
            result.append(definition)
        return result

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        """按名称查找并执行工具 handler。找不到时抛出 KeyError。"""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"tool not found: {name}")
        return await tool.handler(arguments)
