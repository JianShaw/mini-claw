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

    def to_openai_tools(
        self,
        *,
        enabled_tools: list[str] | None = None,
        enabled_mcp_servers: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """生成 OpenAI function calling 格式的工具定义列表。

        enabled_tools: 允许的内置/本地工具名列表，None 表示不过滤。
        enabled_mcp_servers: 允许的 MCP server 名列表，None 表示不过滤。
            MCP 工具命名空间格式为 `server__tool`，匹配 `server` 前缀。
        """
        result: list[dict[str, Any]] = []
        for tool in self._tools.values():
            if not self._tool_allowed(tool.name, enabled_tools, enabled_mcp_servers):
                continue
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

    @staticmethod
    def _tool_allowed(
        name: str,
        enabled_tools: list[str] | None,
        enabled_mcp_servers: list[str] | None,
    ) -> bool:
        """判断工具是否在本轮允许列表中。

        规则：
        - enabled_tools=None, enabled_mcp_servers=None → 全部允许
        - MCP 工具（含 `__`）：按 enabled_mcp_servers 匹配 server 前缀
        - 非 MCP 工具：按 enabled_tools 精确匹配
        """
        if enabled_tools is None and enabled_mcp_servers is None:
            return True
        if "__" in name:
            # MCP 命名空间工具：按 server 前缀匹配
            server = name.split("__", 1)[0]
            if enabled_mcp_servers is not None:
                return server in enabled_mcp_servers
            # enabled_mcp_servers=None 表示不过滤 MCP 工具
            return True
        else:
            # 内置/本地工具
            if enabled_tools is not None:
                return name in enabled_tools
            return True

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        **extra_kwargs: Any,
    ) -> Any:
        """按名称查找并执行工具 handler。找不到时抛出 KeyError。

        extra_kwargs 会合并到 arguments 中，以 `_` 前缀的键传递运行时上下文
        （如 _sandbox_root），不会出现在 LLM 的工具 schema 中。
        """
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"tool not found: {name}")
        merged = {**arguments, **extra_kwargs} if extra_kwargs else arguments
        return await tool.handler(merged)
