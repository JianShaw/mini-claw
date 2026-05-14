"""MCP 数据类型定义：服务器配置、连接状态、资源和提示的元数据。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class McpServerConfig:
    """单个 MCP 服务器配置。

    name:      服务器名称，用作工具命名空间前缀
    transport: 传输方式 (stdio / sse / streamable-http)
    command:   stdio 模式下的可执行命令
    args:      stdio 模式下的命令参数
    env:       stdio 模式下的环境变量（支持 ${VAR} 插值）
    url:       sse / streamable-http 模式的服务器 URL
    headers:   HTTP 模式的自定义头
    disabled:  是否禁用该服务器
    """
    name: str
    transport: str  # "stdio" | "sse" | "streamable-http"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    disabled: bool = False

    def __post_init__(self) -> None:
        """验证配置参数的完整性。"""
        if not self.name or not isinstance(self.name, str):
            raise ValueError("Server name must be a non-empty string")
        if self.transport not in ("stdio", "sse", "streamable-http"):
            raise ValueError(f"Invalid transport: {self.transport}")


@dataclass(slots=True)
class McpServerStatus:
    """服务器连接状态快照，用于 /mcp 命令显示。"""
    name: str
    connected: bool
    tool_count: int = 0
    resource_count: int = 0
    error: str | None = None


@dataclass(slots=True)
class McpResourceInfo:
    """MCP 资源元数据。"""
    server_name: str
    uri: str
    name: str
    description: str | None = None
    mime_type: str | None = None


@dataclass(slots=True)
class McpPromptInfo:
    """MCP 提示模板元数据。"""
    server_name: str
    name: str
    description: str | None = None
    arguments: list[dict[str, Any]] = field(default_factory=list)
