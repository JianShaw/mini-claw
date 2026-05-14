"""MCP 管理器：编排所有 MCP 服务器连接的生命周期和工具注册。

职责：
- 启动/停止所有已配置的 MCP 服务器连接
- 将所有已连接服务器的工具桥接注册到 ToolsRegistry
- 聚合所有服务器的资源和提示
- 提供状态查询接口（用于 /mcp 命令）
"""

from __future__ import annotations

import logging
from typing import Any

from claw.mcp.bridge import register_mcp_tools
from claw.mcp.config import McpConfigLoader
from claw.mcp.connection import McpServerConnection
from claw.mcp.types import (
    McpPromptInfo,
    McpResourceInfo,
    McpServerConfig,
    McpServerStatus,
)
from claw.tools import ToolsRegistry

logger = logging.getLogger(__name__)


class McpManager:
    """MCP 服务器连接管理器，管理多个 McpServerConnection 的生命周期。"""

    def __init__(self, configs: list[McpServerConfig]) -> None:
        """初始化管理器。

        Args:
            configs: MCP 服务器配置列表
        """
        self._configs = configs
        self._connections: dict[str, McpServerConnection] = {}

    @classmethod
    def from_config_file(cls, path: str) -> McpManager:
        """从配置文件创建管理器的工厂方法。

        Args:
            path: mcp_config.json 文件路径

        Returns:
            配置好的 McpManager 实例
        """
        configs = McpConfigLoader.load(path)
        return cls(configs)

    async def start(self) -> None:
        """连接所有已启用的服务器。单个服务器失败不阻塞其他服务器。"""
        for config in self._configs:
            if config.disabled:
                logger.info("MCP server '%s' is disabled, skipping", config.name)
                continue
            connection = McpServerConnection(config)
            self._connections[config.name] = connection
            try:
                await connection.connect()
            except Exception as exc:
                logger.warning(
                    "MCP server '%s' failed to start: %s", config.name, exc
                )

    async def stop(self) -> None:
        """断开所有服务器连接。"""
        for name, connection in self._connections.items():
            try:
                await connection.disconnect()
            except Exception as exc:
                logger.warning("Error stopping MCP server '%s': %s", name, exc)
        self._connections.clear()

    def register_tools(self, registry: ToolsRegistry) -> list[str]:
        """将所有已连接服务器的工具桥接注册到 ToolsRegistry。

        Args:
            registry: 工具注册表

        Returns:
            成功注册的所有工具名列表
        """
        all_registered: list[str] = []
        for connection in self._connections.values():
            if connection.connected:
                registered = register_mcp_tools(connection, registry)
                all_registered.extend(registered)
        return all_registered

    def list_resources(self) -> list[McpResourceInfo]:
        """聚合所有已连接服务器的资源列表。"""
        result: list[McpResourceInfo] = []
        for connection in self._connections.values():
            if connection.connected:
                result.extend(connection.resources)
        return result

    def list_prompts(self) -> list[McpPromptInfo]:
        """聚合所有已连接服务器的提示列表。"""
        result: list[McpPromptInfo] = []
        for connection in self._connections.values():
            if connection.connected:
                result.extend(connection.prompts)
        return result

    async def read_resource(self, server_name: str, uri: str) -> str:
        """按服务器名路由资源读取请求。"""
        connection = self._connections.get(server_name)
        if connection is None or not connection.connected:
            return f"[MCP error: server '{server_name}' not found or not connected]"
        return await connection.read_resource(uri)

    async def get_prompt(
        self, server_name: str, name: str, arguments: dict[str, str] | None = None
    ) -> str:
        """按服务器名路由提示获取请求。"""
        connection = self._connections.get(server_name)
        if connection is None or not connection.connected:
            return f"[MCP error: server '{server_name}' not found or not connected]"
        return await connection.get_prompt(name, arguments)

    def get_status(self) -> list[McpServerStatus]:
        """返回所有服务器的连接状态快照，用于 /mcp 命令。"""
        statuses: list[McpServerStatus] = []
        for name, connection in self._connections.items():
            statuses.append(McpServerStatus(
                name=name,
                connected=connection.connected,
                tool_count=len(connection.tools),
                resource_count=len(connection.resources),
                error=connection.error,
            ))
        # 包含被禁用的服务器
        configured_names = {c.name for c in self._configs}
        active_names = set(self._connections.keys())
        for config in self._configs:
            if config.name not in active_names and config.disabled:
                statuses.append(McpServerStatus(
                    name=config.name,
                    connected=False,
                    error="disabled",
                ))
        return statuses

    async def reconnect(self, server_name: str) -> bool:
        """重新连接指定服务器。

        Returns:
            是否重连成功
        """
        connection = self._connections.get(server_name)
        if connection is None:
            logger.warning("Server '%s' not found for reconnect", server_name)
            return False
        try:
            await connection.disconnect()
            await connection.connect()
            return connection.connected
        except Exception as exc:
            logger.warning("Reconnect '%s' failed: %s", server_name, exc)
            return False
