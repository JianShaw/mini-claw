"""MCP 服务器连接测试：验证后台 Task + 队列架构的连接生命周期。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claw.mcp.connection import McpServerConnection
from claw.mcp.types import McpServerConfig


def _stdio_config() -> McpServerConfig:
    return McpServerConfig(name="test-server", transport="stdio", command="python", args=["-m", "test"])


def _make_mock_tool(name: str, description: str = "desc", input_schema: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, description=description, inputSchema=input_schema or {"type": "object", "properties": {}})


def _make_transport_cm(read_stream=None, write_stream=None):
    """创建模拟的 transport async context manager。"""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=(read_stream or AsyncMock(), write_stream or AsyncMock()))
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class _MockClientSession:
    """模拟 ClientSession，支持 async with 和 call_tool/list_tools。"""

    def __init__(self):
        self.initialized = False
        self._tool_result = SimpleNamespace(content=[SimpleNamespace(text="mock result")])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        return SimpleNamespace(tools=[
            _make_mock_tool("read_file", "Read a file"),
            _make_mock_tool("write_file", "Write a file"),
        ])

    async def list_resources(self):
        # 模拟不支持 resources
        raise Exception("Method not found")

    async def list_prompts(self):
        # 模拟不支持 prompts
        raise Exception("Method not found")

    async def call_tool(self, name, arguments):
        return self._tool_result

    async def read_resource(self, uri):
        return SimpleNamespace(contents=[SimpleNamespace(text="resource data")])

    async def get_prompt(self, name, arguments):
        return SimpleNamespace(
            messages=[SimpleNamespace(content=SimpleNamespace(text="prompt text"))]
        )


class TestMcpServerConnection:
    """McpServerConnection 测试套件。"""

    async def test_connect_discovers_tools(self) -> None:
        """连接成功后应发现工具。"""
        config = _stdio_config()
        conn = McpServerConnection(config)
        transport_cm = _make_transport_cm()

        with patch("claw.mcp.connection.stdio_client", return_value=transport_cm), \
             patch("claw.mcp.connection.ClientSession", return_value=_MockClientSession()):
            await conn.connect()

        assert conn.connected is True
        assert len(conn.tools) == 2
        assert conn.error is None

        await conn.disconnect()

    async def test_connect_failure_sets_error(self) -> None:
        """连接失败时应设置错误信息。"""
        config = _stdio_config()
        conn = McpServerConnection(config)

        transport_cm = AsyncMock()
        transport_cm.__aenter__ = AsyncMock(side_effect=ConnectionError("refused"))
        transport_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("claw.mcp.connection.stdio_client", return_value=transport_cm):
            await conn.connect()

        assert conn.connected is False
        assert conn.error is not None
        assert "refused" in conn.error

    async def test_call_tool_success(self) -> None:
        """调用工具应通过队列路由到后台 Task 执行并返回结果。"""
        config = _stdio_config()
        conn = McpServerConnection(config)
        transport_cm = _make_transport_cm()

        with patch("claw.mcp.connection.stdio_client", return_value=transport_cm), \
             patch("claw.mcp.connection.ClientSession", return_value=_MockClientSession()):
            await conn.connect()

        result = await conn.call_tool("read_file", {"path": "/a.txt"})
        assert result == "mock result"

        await conn.disconnect()

    async def test_call_tool_not_connected(self) -> None:
        """未连接时调用工具应返回错误字符串。"""
        config = _stdio_config()
        conn = McpServerConnection(config)
        result = await conn.call_tool("read_file", {})
        assert "not connected" in result

    async def test_read_resource_success(self) -> None:
        """读取资源应通过后台 Task 返回内容。"""
        config = _stdio_config()
        conn = McpServerConnection(config)
        transport_cm = _make_transport_cm()

        with patch("claw.mcp.connection.stdio_client", return_value=transport_cm), \
             patch("claw.mcp.connection.ClientSession", return_value=_MockClientSession()):
            await conn.connect()

        result = await conn.read_resource("file:///a.txt")
        assert result == "resource data"

        await conn.disconnect()

    async def test_read_resource_not_connected(self) -> None:
        """未连接时读取资源应返回错误字符串。"""
        config = _stdio_config()
        conn = McpServerConnection(config)
        result = await conn.read_resource("file:///a.txt")
        assert "not connected" in result

    async def test_get_prompt_success(self) -> None:
        """获取提示应通过后台 Task 返回内容。"""
        config = _stdio_config()
        conn = McpServerConnection(config)
        transport_cm = _make_transport_cm()

        with patch("claw.mcp.connection.stdio_client", return_value=transport_cm), \
             patch("claw.mcp.connection.ClientSession", return_value=_MockClientSession()):
            await conn.connect()

        result = await conn.get_prompt("greet", {"name": "World"})
        assert "prompt text" in result

        await conn.disconnect()

    async def test_get_prompt_not_connected(self) -> None:
        """未连接时获取提示应返回错误字符串。"""
        config = _stdio_config()
        conn = McpServerConnection(config)
        result = await conn.get_prompt("greet")
        assert "not connected" in result

    async def test_disconnect_clears_state(self) -> None:
        """断开连接应清理所有缓存状态。"""
        config = _stdio_config()
        conn = McpServerConnection(config)
        transport_cm = _make_transport_cm()

        with patch("claw.mcp.connection.stdio_client", return_value=transport_cm), \
             patch("claw.mcp.connection.ClientSession", return_value=_MockClientSession()):
            await conn.connect()

        assert conn.connected is True
        await conn.disconnect()

        assert conn.connected is False
        assert conn.tools == []
        assert conn._background_task is None

    async def test_call_tool_timeout(self) -> None:
        """工具调用超时应返回错误字符串。"""
        config = _stdio_config()
        conn = McpServerConnection(config)

        # 手动设置连接状态但不启动后台 Task
        conn._connected = True
        conn._stop_event = asyncio.Event()
        conn._background_task = None

        # 提交请求但没有人处理
        result = await conn._submit_request("call_tool", ("test", {}), timeout=0.5)
        assert "timed out" in result

        conn._connected = False

    async def test_disconnect_rejects_pending(self) -> None:
        """断开时应拒绝等待中的请求。"""
        config = _stdio_config()
        conn = McpServerConnection(config)
        conn._connected = True

        # 创建一个 pending future
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        conn._pending["test-id"] = future

        await conn.disconnect()
        assert conn._pending == {}
