"""网页搜索工具：基于 duckduckgo-search 的搜索和 URL 抓取。

安全策略：
- web_fetch 仅允许 http/https 协议
- 阻止私有 IP 地址和 localhost
- 限制响应大小
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from claw.tools import Tool, ToolsRegistry

# 私有 IP 范围
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique-local
]


def _is_private_host(hostname: str) -> bool:
    """检查主机名是否解析到私有 IP 地址。"""
    try:
        import socket
        addr_info = socket.getaddrinfo(hostname, None)
        for info in addr_info:
            ip = ipaddress.ip_address(info[4][0])
            for network in _PRIVATE_NETWORKS:
                if ip in network:
                    return True
    except (socket.gaierror, ValueError):
        # 无法解析的地址视为不安全
        return True
    return False


async def _web_search(args: dict[str, Any]) -> str:
    query = args["query"]
    max_results = args.get("max_results", 5)
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "No results found."
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', 'No title')}")
            lines.append(f"   {r.get('href', '')}")
            lines.append(f"   {r.get('body', '')}")
            lines.append("")
        return "\n".join(lines)
    except ImportError:
        return "Error: duckduckgo-search not installed. Run: uv add duckduckgo-search"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


async def _web_fetch(args: dict[str, Any]) -> str:
    url = args["url"]
    max_length = args.get("max_length", 5000)

    # 协议检查：仅允许 http 和 https
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Error: unsupported scheme '{parsed.scheme}', only http/https allowed"
    if not parsed.hostname:
        return "Error: invalid URL, no hostname"

    # SSRF 防护：阻止私有网络地址
    if _is_private_host(parsed.hostname):
        return f"Error: access to private/local network addresses is blocked: {parsed.hostname}"

    try:
        with urlopen(url, timeout=10) as resp:
            content = resp.read(max_length + 1).decode("utf-8", errors="replace")
        if len(content) > max_length:
            content = content[:max_length] + "\n... (truncated)"
        return content
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def register(registry: ToolsRegistry) -> None:
    registry.register(Tool(
        name="web_search",
        description="Search the web using DuckDuckGo and return results.",
        handler=_web_search,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max number of results (default 5)"},
            },
            "required": ["query"],
        },
    ))
    registry.register(Tool(
        name="web_fetch",
        description="Fetch the content of a web page by URL. Only http/https URLs allowed. Private/local network addresses are blocked.",
        handler=_web_fetch,
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch (http/https only)"},
                "max_length": {"type": "integer", "description": "Max content length in chars (default 5000)"},
            },
            "required": ["url"],
        },
    ))
