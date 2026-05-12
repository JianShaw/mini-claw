"""工具注册表：预留 Agent 可调用的工具扩展点。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(slots=True)
class Tool:
    """工具定义：名称、描述、异步处理函数。"""
    name: str
    description: str
    handler: ToolHandler


class ToolsRegistry:
    """工具注册表：注册、查找、列举工具。不允许重复注册同名工具。"""

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
