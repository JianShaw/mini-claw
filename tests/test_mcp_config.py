"""MCP 配置加载器测试：验证文件解析、环境变量插值和边界情况。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claw.mcp.config import McpConfigLoader
from claw.mcp.types import McpServerConfig


def _write_config(tmp_path: Path, data: dict) -> Path:
    """写入临时配置文件并返回路径。"""
    p = tmp_path / "mcp_config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


class TestMcpConfigLoader:
    """McpConfigLoader 测试套件。"""

    def test_load_returns_empty_when_file_not_exists(self, tmp_path: Path) -> None:
        """文件不存在时应返回空列表。"""
        result = McpConfigLoader.load(tmp_path / "nonexistent.json")
        assert result == []

    def test_load_returns_empty_for_empty_json(self, tmp_path: Path) -> None:
        """空 JSON 对象应返回空列表。"""
        p = _write_config(tmp_path, {})
        result = McpConfigLoader.load(p)
        assert result == []

    def test_load_returns_empty_for_invalid_json(self, tmp_path: Path) -> None:
        """无效 JSON 应返回空列表。"""
        p = tmp_path / "bad.json"
        p.write_text("{invalid json", encoding="utf-8")
        result = McpConfigLoader.load(p)
        assert result == []

    def test_load_stdio_config(self, tmp_path: Path) -> None:
        """应正确解析 stdio 传输配置。"""
        p = _write_config(tmp_path, {
            "mcpServers": {
                "filesystem": {
                    "transport": "stdio",
                    "command": "python",
                    "args": ["-m", "mcp_server"],
                }
            }
        })
        result = McpConfigLoader.load(p)
        assert len(result) == 1
        assert result[0].name == "filesystem"
        assert result[0].transport == "stdio"
        assert result[0].command == "python"
        assert result[0].args == ["-m", "mcp_server"]

    def test_load_sse_config(self, tmp_path: Path) -> None:
        """应正确解析 sse 传输配置。"""
        p = _write_config(tmp_path, {
            "mcpServers": {
                "remote": {
                    "transport": "sse",
                    "url": "http://localhost:8080/sse",
                    "headers": {"Authorization": "Bearer token"},
                }
            }
        })
        result = McpConfigLoader.load(p)
        assert len(result) == 1
        assert result[0].name == "remote"
        assert result[0].transport == "sse"
        assert result[0].url == "http://localhost:8080/sse"
        assert result[0].headers == {"Authorization": "Bearer token"}

    def test_load_streamable_http_config(self, tmp_path: Path) -> None:
        """应正确解析 streamable-http 传输配置。"""
        p = _write_config(tmp_path, {
            "mcpServers": {
                "api": {
                    "transport": "streamable-http",
                    "url": "http://localhost:9000/mcp",
                }
            }
        })
        result = McpConfigLoader.load(p)
        assert len(result) == 1
        assert result[0].transport == "streamable-http"
        assert result[0].url == "http://localhost:9000/mcp"

    def test_load_env_interpolation(self, tmp_path: Path, monkeypatch) -> None:
        """应将 ${VAR} 替换为环境变量值。"""
        monkeypatch.setenv("MY_WORKSPACE", "/my/workspace")
        monkeypatch.setenv("MY_KEY", "secret123")
        p = _write_config(tmp_path, {
            "mcpServers": {
                "fs": {
                    "transport": "stdio",
                    "command": "python",
                    "args": [],
                    "env": {
                        "WORKSPACE": "${MY_WORKSPACE}",
                        "API_KEY": "${MY_KEY}",
                    },
                }
            }
        })
        result = McpConfigLoader.load(p)
        assert result[0].env["WORKSPACE"] == "/my/workspace"
        assert result[0].env["API_KEY"] == "secret123"

    def test_load_env_undefined_replaced_with_empty(self, tmp_path: Path) -> None:
        """未定义的环境变量应替换为空字符串。"""
        p = _write_config(tmp_path, {
            "mcpServers": {
                "fs": {
                    "transport": "stdio",
                    "command": "python",
                    "env": {"MISSING": "${UNDEFINED_VAR_XYZ}"},
                }
            }
        })
        result = McpConfigLoader.load(p)
        assert result[0].env["MISSING"] == ""

    def test_load_disabled_server(self, tmp_path: Path) -> None:
        """disabled 为 true 的服务器应被解析但标记为禁用。"""
        p = _write_config(tmp_path, {
            "mcpServers": {
                "off": {
                    "transport": "stdio",
                    "command": "python",
                    "disabled": True,
                }
            }
        })
        result = McpConfigLoader.load(p)
        assert len(result) == 1
        assert result[0].disabled is True

    def test_load_multiple_servers(self, tmp_path: Path) -> None:
        """应正确解析多个服务器配置。"""
        p = _write_config(tmp_path, {
            "mcpServers": {
                "a": {"transport": "stdio", "command": "a"},
                "b": {"transport": "sse", "url": "http://b"},
                "c": {"transport": "streamable-http", "url": "http://c"},
            }
        })
        result = McpConfigLoader.load(p)
        assert len(result) == 3
        names = {r.name for r in result}
        assert names == {"a", "b", "c"}

    def test_load_skips_unknown_transport(self, tmp_path: Path) -> None:
        """未知传输方式应被跳过。"""
        p = _write_config(tmp_path, {
            "mcpServers": {
                "bad": {"transport": "websocket"},
            }
        })
        result = McpConfigLoader.load(p)
        assert result == []

    def test_load_skips_invalid_server_entry(self, tmp_path: Path) -> None:
        """非字典的服务器配置应被跳过。"""
        p = _write_config(tmp_path, {
            "mcpServers": {
                "bad": "not a dict",
                "good": {"transport": "stdio", "command": "python"},
            }
        })
        result = McpConfigLoader.load(p)
        assert len(result) == 1
        assert result[0].name == "good"

    def test_load_defaults_transport_to_stdio(self, tmp_path: Path) -> None:
        """未指定 transport 时默认为 stdio。"""
        p = _write_config(tmp_path, {
            "mcpServers": {
                "default": {"command": "python"},
            }
        })
        result = McpConfigLoader.load(p)
        assert result[0].transport == "stdio"

    def test_load_not_disabled_by_default(self, tmp_path: Path) -> None:
        """未指定 disabled 时默认为 False。"""
        p = _write_config(tmp_path, {
            "mcpServers": {
                "s": {"transport": "stdio", "command": "python"},
            }
        })
        result = McpConfigLoader.load(p)
        assert result[0].disabled is False
