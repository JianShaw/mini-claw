"""MCP 工具桥接测试：验证命名空间、handler 委托、冲突处理。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from claw.mcp.bridge import namespaced_name, register_mcp_tools
from claw.mcp.connection import McpServerConnection
from claw.mcp.types import McpServerConfig
from claw.tools import Tool, ToolsRegistry


def _make_mock_tool(name: str, description: str = "desc", schema: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, description=description, inputSchema=schema)


def _make_connection_with_tools(tools: list[SimpleNamespace]) -> McpServerConnection:
    """创建已连接的 mock 连接，包含指定工具。"""
    config = McpServerConfig(name="test-srv", transport="stdio", command="python")
    conn = McpServerConnection(config)
    conn._connected = True
    conn._tools = tools
    return conn


class TestNamespacedName:
    """命名空间工具名生成测试。"""

    def test_basic(self) -> None:
        assert namespaced_name("fs", "read") == "fs__read"

    def test_with_hyphen(self) -> None:
        assert namespaced_name("my-server", "tool") == "my-server__tool"


class TestRegisterMcpTools:
    """工具注册桥接测试。"""

    async def test_registers_namespaced_tools(self) -> None:
        """应将 MCP 工具注册为带命名空间前缀的 Tool。"""
        registry = ToolsRegistry()
        conn = _make_connection_with_tools([
            _make_mock_tool("read_file", "Read a file"),
            _make_mock_tool("write_file", "Write a file"),
        ])

        # 模拟 call_tool 返回
        conn._session = AsyncMock()
        conn._session.call_tool = AsyncMock(return_value=SimpleNamespace(
            content=[SimpleNamespace(text="ok")]
        ))

        registered = register_mcp_tools(conn, registry)
        assert registered == ["test-srv__read_file", "test-srv__write_file"]
        assert registry.get("test-srv__read_file") is not None
        assert registry.get("test-srv__write_file") is not None

    async def test_handler_delegates_to_connection(self) -> None:
        """handler 应委托到 connection.call_tool 并传递原始工具名。"""
        registry = ToolsRegistry()
        conn = _make_connection_with_tools([_make_mock_tool("read")])
        # mock call_tool 以验证委托（不需要后台 Task）
        conn.call_tool = AsyncMock(return_value="file content")

        register_mcp_tools(conn, registry)
        result = await registry.execute("test-srv__read", {"path": "/a"})
        assert result == "file content"
        conn.call_tool.assert_called_once_with("read", {"path": "/a"})

    async def test_parameters_mapping(self) -> None:
        """应正确映射 MCP 工具的参数 schema。"""
        schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        registry = ToolsRegistry()
        conn = _make_connection_with_tools([_make_mock_tool("read", schema=schema)])

        register_mcp_tools(conn, registry)
        tool = registry.get("test-srv__read")
        assert tool is not None
        assert tool.parameters == schema

    async def test_skips_conflicting_tool_name(self) -> None:
        """工具名冲突时应跳过并继续注册其他工具。"""
        registry = ToolsRegistry()
        # 预注册一个冲突工具
        registry.register(Tool(
            name="test-srv__read",
            description="existing",
            handler=AsyncMock(return_value=""),
        ))
        conn = _make_connection_with_tools([
            _make_mock_tool("read"),   # 冲突
            _make_mock_tool("write"),  # 不冲突
        ])

        registered = register_mcp_tools(conn, registry)
        assert registered == ["test-srv__write"]

    async def test_empty_tools_list(self) -> None:
        """无工具时应返回空列表。"""
        registry = ToolsRegistry()
        conn = _make_connection_with_tools([])
        registered = register_mcp_tools(conn, registry)
        assert registered == []

    async def test_description_passed_through(self) -> None:
        """工具描述应正确传递。"""
        registry = ToolsRegistry()
        conn = _make_connection_with_tools([_make_mock_tool("t", "My description")])
        register_mcp_tools(conn, registry)
        tool = registry.get("test-srv__t")
        assert tool is not None
        assert tool.description == "My description"

    async def test_no_schema_uses_default(self) -> None:
        """无 inputSchema 时应使用空 object 默认值。"""
        registry = ToolsRegistry()
        tool = _make_mock_tool("t")
        tool.inputSchema = None
        conn = _make_connection_with_tools([tool])
        register_mcp_tools(conn, registry)
        reg_tool = registry.get("test-srv__t")
        assert reg_tool is not None
        assert reg_tool.parameters == {"type": "object", "properties": {}}
