"""MCP 配置加载器：解析 mcp_config.json，支持环境变量插值。"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from claw.mcp.types import McpServerConfig

logger = logging.getLogger(__name__)

# 匹配 ${VAR_NAME} 格式的环境变量引用
_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env(value: str) -> str:
    """将字符串中的 ${VAR_NAME} 替换为环境变量值，未定义时替换为空字符串。"""
    def _replacer(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), "")
    return _ENV_PATTERN.sub(_replacer, value)


def _resolve_env_dict(env: dict[str, str]) -> dict[str, str]:
    """对字典中的所有值执行环境变量插值。"""
    return {k: _resolve_env(v) for k, v in env.items()}


class McpConfigLoader:
    """从 JSON 文件加载 MCP 服务器配置列表。

    文件不存在时静默返回空列表，MCP 是可选功能。
    """

    @staticmethod
    def load(path: str | Path) -> list[McpServerConfig]:
        """加载配置文件并返回服务器配置列表。

        Args:
            path: 配置文件路径

        Returns:
            解析后的服务器配置列表。文件不存在或为空时返回 []。
        """
        config_path = Path(path)
        if not config_path.exists():
            logger.debug("MCP config file not found: %s", path)
            return []

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load MCP config %s: %s", path, exc)
            return []

        return McpConfigLoader._parse(data)

    @staticmethod
    def _parse(data: dict[str, Any]) -> list[McpServerConfig]:
        """解析 JSON 数据为 McpServerConfig 列表。"""
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            return []

        configs: list[McpServerConfig] = []
        for name, server_data in servers.items():
            if not isinstance(server_data, dict):
                logger.warning("Skipping invalid server config for '%s'", name)
                continue

            config = McpConfigLoader._parse_server(name, server_data)
            if config is not None:
                configs.append(config)

        return configs

    @staticmethod
    def _parse_server(name: str, data: dict[str, Any]) -> McpServerConfig | None:
        """解析单个服务器配置。"""
        transport = data.get("transport", "stdio")
        if transport not in ("stdio", "sse", "streamable-http"):
            logger.warning("Unknown transport '%s' for server '%s', skipping", transport, name)
            return None

        config = McpServerConfig(
            name=name,
            transport=transport,
            disabled=data.get("disabled", False),
        )

        # stdio 模式参数
        if transport == "stdio":
            config.command = data.get("command")
            config.args = data.get("args", [])
            raw_env = data.get("env", {})
            config.env = _resolve_env_dict(raw_env) if isinstance(raw_env, dict) else {}

        # HTTP 模式参数
        if transport in ("sse", "streamable-http"):
            config.url = data.get("url")
            config.headers = data.get("headers", {})

        return config
