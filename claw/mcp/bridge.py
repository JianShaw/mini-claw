"""MCP 工具桥接：将 MCP 服务器工具转换为 ToolsRegistry 可识别的 Tool 对象。

核心转换：
- MCP 工具名 → `{server_name}__{tool_name}` 命名空间格式
- MCP 工具参数 schema → OpenAI function calling 的 parameters 格式
- MCP 工具调用 → 通过 McpServerConnection.call_tool() 代理执行
"""

from __future__ import annotations

import logging
from typing import Any

from claw.mcp.connection import McpServerConnection
from claw.tools import Tool, ToolsRegistry

logger = logging.getLogger(__name__)

# 命名空间分隔符
NAMESPACE_SEP = "__"


def namespaced_name(server_name: str, tool_name: str) -> str:
    """生成带命名空间的工具名：`server__tool`。"""
    return f"{server_name}{NAMESPACE_SEP}{tool_name}"


def _convert_parameters(mcp_tool: Any) -> dict[str, Any]:
    """将 MCP 工具的参数 schema 转换为 OpenAI function calling 格式。

    MCP 工具的 inputSchema 已经是 JSON Schema 格式，通常可以直接使用。
    """
    schema = getattr(mcp_tool, "inputSchema", None)
    if schema and isinstance(schema, dict):
        return schema
    return {"type": "object", "properties": {}}


def _create_handler(
    connection: McpServerConnection,
    original_name: str,
) -> Any:
    """创建异步闭包作为 Tool 的 handler，代理调用 MCP 工具。

    Args:
        connection: MCP 服务器连接
        original_name: MCP 工具的原始名称（不带命名空间前缀）

    Returns:
        异步函数，接收 arguments dict，返回调用结果字符串
    """
    async def handler(arguments: dict[str, Any]) -> str:
        return await connection.call_tool(original_name, arguments)
    return handler


def register_mcp_tools(
    connection: McpServerConnection,
    registry: ToolsRegistry,
) -> list[str]:
    """将 MCP 服务器的所有工具注册到 ToolsRegistry。

    工具名使用 `{server_name}__{tool_name}` 格式。如果工具名与已有工具冲突，
    跳过并记录警告。

    Args:
        connection: 已连接的 MCP 服务器连接
        registry: 工具注册表

    Returns:
        成功注册的工具名列表（带命名空间前缀）
    """
    registered: list[str] = []
    for mcp_tool in connection.tools:
        original_name = mcp_tool.name

        # 跳过无效工具名
        if not original_name or not isinstance(original_name, str):
            logger.warning("Invalid tool name from server '%s', skipping", connection.name)
            continue

        full_name = namespaced_name(connection.name, original_name)

        # 检查是否与已有工具冲突
        if registry.get(full_name) is not None:
            logger.warning(
                "MCP tool '%s' conflicts with existing tool, skipping", full_name
            )
            continue

        description = getattr(mcp_tool, "description", "") or ""
        parameters = _convert_parameters(mcp_tool)
        handler = _create_handler(connection, original_name)

        tool = Tool(
            name=full_name,
            description=description,
            handler=handler,
            parameters=parameters,
        )
        try:
            registry.register(tool)
            registered.append(full_name)
        except ValueError:
            # 并发注册时可能冲突，跳过
            logger.warning("MCP tool '%s' registration conflict, skipping", full_name)

    return registered
