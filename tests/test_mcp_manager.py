"""MCP 管理器测试：验证多服务器编排、失败隔离和工具注册。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claw.mcp.manager import McpManager
from claw.mcp.types import McpServerConfig, McpServerStatus
from claw.tools import Tool, ToolsRegistry


def _config(name: str, transport: str = "stdio", disabled: bool = False) -> McpServerConfig:
    return McpServerConfig(
        name=name,
        transport=transport,
        command="python" if transport == "stdio" else None,
        url="http://localhost" if transport != "stdio" else None,
        disabled=disabled,
    )


class TestMcpManagerStartStop:
    """McpManager 启停测试。"""

    async def test_start_connects_all_enabled_servers(self) -> None:
        """start 应连接所有启用的服务器。"""
        configs = [_config("a"), _config("b")]
        manager = McpManager(configs)

        with patch("claw.mcp.manager.McpServerConnection") as MockConn:
            mock_conn = AsyncMock()
            mock_conn.connected = True
            mock_conn.tools = []
            mock_conn.resources = []
            mock_conn.prompts = []
            mock_conn.error = None
            MockConn.return_value = mock_conn

            await manager.start()

        assert len(manager._connections) == 2

    async def test_start_skips_disabled_servers(self) -> None:
        """disabled 服务器不应被连接。"""
        configs = [_config("a"), _config("b", disabled=True)]
        manager = McpManager(configs)

        with patch("claw.mcp.manager.McpServerConnection") as MockConn:
            mock_conn = AsyncMock()
            mock_conn.connected = True
            mock_conn.tools = []
            mock_conn.resources = []
            mock_conn.prompts = []
            MockConn.return_value = mock_conn

            await manager.start()

        assert "a" in manager._connections
        assert "b" not in manager._connections

    async def test_start_failure_does_not_block_others(self) -> None:
        """单个服务器连接失败不应阻塞其他服务器。"""
        configs = [_config("good"), _config("bad")]
        manager = McpManager(configs)

        with patch("claw.mcp.manager.McpServerConnection") as MockConn:
            connections = []
            for cfg in configs:
                mock_conn = MagicMock()
                mock_conn.config = cfg
                mock_conn.name = cfg.name
                if cfg.name == "bad":
                    mock_conn.connect = AsyncMock(side_effect=ConnectionError("fail"))
                else:
                    mock_conn.connect = AsyncMock()
                mock_conn.disconnect = AsyncMock()
                mock_conn.connected = cfg.name != "bad"
                mock_conn.tools = []
                mock_conn.resources = []
                mock_conn.prompts = []
                mock_conn.error = "fail" if cfg.name == "bad" else None
                connections.append(mock_conn)
            MockConn.side_effect = connections

            await manager.start()

        # "good" 应该连接，"bad" 的连接对象存在但连接失败
        assert "good" in manager._connections
        assert "bad" in manager._connections

    async def test_stop_disconnects_all(self) -> None:
        """stop 应断开所有连接并清空连接字典。"""
        configs = [_config("a")]
        manager = McpManager(configs)

        with patch("claw.mcp.manager.McpServerConnection") as MockConn:
            mock_conn = AsyncMock()
            mock_conn.connected = True
            mock_conn.disconnect = AsyncMock()
            MockConn.return_value = mock_conn
            await manager.start()
            await manager.stop()

        assert len(manager._connections) == 0


class TestMcpManagerRegisterTools:
    """工具注册测试。"""

    async def test_register_tools_bridges_connected_servers(self) -> None:
        """register_tools 应桥接所有已连接服务器的工具。"""
        configs = [_config("srv")]
        manager = McpManager(configs)
        registry = ToolsRegistry()

        mock_conn = MagicMock()
        mock_conn.connected = True
        mock_conn.name = "srv"
        mock_conn.tools = [SimpleNamespace(name="t1", description="d", inputSchema={"type": "object", "properties": {}})]
        mock_conn.call_tool = AsyncMock(return_value="ok")
        mock_conn._session = AsyncMock()
        mock_conn._session.call_tool = AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(text="ok")]))
        manager._connections["srv"] = mock_conn

        registered = manager.register_tools(registry)
        assert "srv__t1" in registered
        assert registry.get("srv__t1") is not None

    async def test_register_tools_skips_disconnected(self) -> None:
        """register_tools 应跳过未连接的服务器。"""
        configs = [_config("srv")]
        manager = McpManager(configs)
        registry = ToolsRegistry()

        mock_conn = MagicMock()
        mock_conn.connected = False
        manager._connections["srv"] = mock_conn

        registered = manager.register_tools(registry)
        assert registered == []


class TestMcpManagerResourcesPrompts:
    """资源和提示聚合测试。"""

    def test_list_resources_aggregates(self) -> None:
        """list_resources 应聚合所有已连接服务器的资源。"""
        from claw.mcp.types import McpResourceInfo
        configs = [_config("a"), _config("b")]
        manager = McpManager(configs)

        conn_a = MagicMock()
        conn_a.connected = True
        conn_a.resources = [McpResourceInfo(server_name="a", uri="file:///a", name="a")]
        conn_b = MagicMock()
        conn_b.connected = True
        conn_b.resources = [McpResourceInfo(server_name="b", uri="file:///b", name="b")]
        manager._connections = {"a": conn_a, "b": conn_b}

        result = manager.list_resources()
        assert len(result) == 2

    def test_list_prompts_aggregates(self) -> None:
        """list_prompts 应聚合所有已连接服务器的提示。"""
        from claw.mcp.types import McpPromptInfo
        configs = [_config("a")]
        manager = McpManager(configs)

        conn = MagicMock()
        conn.connected = True
        conn.prompts = [McpPromptInfo(server_name="a", name="greet")]
        manager._connections = {"a": conn}

        result = manager.list_prompts()
        assert len(result) == 1

    async def test_read_resource_routes_to_server(self) -> None:
        """read_resource 应路由到正确的服务器。"""
        configs = [_config("srv")]
        manager = McpManager(configs)

        mock_conn = MagicMock()
        mock_conn.connected = True
        mock_conn.read_resource = AsyncMock(return_value="data")
        manager._connections["srv"] = mock_conn

        result = await manager.read_resource("srv", "file:///a")
        assert result == "data"
        mock_conn.read_resource.assert_called_once_with("file:///a")

    async def test_read_resource_unknown_server(self) -> None:
        """请求不存在的服务器应返回错误字符串。"""
        manager = McpManager([])
        result = await manager.read_resource("unknown", "file:///a")
        assert "not found" in result


class TestMcpManagerStatus:
    """状态查询测试。"""

    def test_get_status(self) -> None:
        """get_status 应返回所有服务器的状态快照。"""
        configs = [_config("a"), _config("b", disabled=True)]
        manager = McpManager(configs)

        conn_a = MagicMock()
        conn_a.connected = True
        conn_a.tools = [1, 2]
        conn_a.resources = [1]
        conn_a.error = None
        manager._connections["a"] = conn_a

        statuses = manager.get_status()
        assert len(statuses) == 2
        connected = [s for s in statuses if s.connected]
        assert len(connected) == 1
        assert connected[0].name == "a"
        assert connected[0].tool_count == 2

    def test_get_status_empty(self) -> None:
        """无配置时应返回空列表。"""
        manager = McpManager([])
        assert manager.get_status() == []


class TestMcpManagerReconnect:
    """重连测试。"""

    async def test_reconnect_success(self) -> None:
        """reconnect 应先断开再连接。"""
        configs = [_config("srv")]
        manager = McpManager(configs)

        mock_conn = AsyncMock()
        mock_conn.connected = True
        manager._connections["srv"] = mock_conn

        result = await manager.reconnect("srv")
        assert result is True
        mock_conn.disconnect.assert_called_once()
        mock_conn.connect.assert_called_once()

    async def test_reconnect_unknown_server(self) -> None:
        """重连不存在的服务器应返回 False。"""
        manager = McpManager([])
        result = await manager.reconnect("unknown")
        assert result is False


class TestMcpManagerFromConfigFile:
    """工厂方法测试。"""

    def test_from_config_file(self, tmp_path) -> None:
        """应从配置文件创建管理器。"""
        import json
        p = tmp_path / "mcp_config.json"
        p.write_text(json.dumps({
            "mcpServers": {
                "fs": {"transport": "stdio", "command": "python"},
            }
        }))
        manager = McpManager.from_config_file(str(p))
        assert len(manager._configs) == 1
        assert manager._configs[0].name == "fs"

    def test_from_config_file_not_exists(self, tmp_path) -> None:
        """配置文件不存在应创建空管理器。"""
        manager = McpManager.from_config_file(str(tmp_path / "nonexistent.json"))
        assert manager._configs == []
