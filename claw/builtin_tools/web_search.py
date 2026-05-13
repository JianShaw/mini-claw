"""网页搜索工具：基于 duckduckgo-search 的搜索和 URL 抓取。

安全策略：
- web_fetch 仅允许 http/https 协议
- 阻止私有 IP 地址和 localhost
- 手动处理重定向，每次重定向重新校验目标 host（防止 SSRF）
- 限制响应大小
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPError, Request, build_opener

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

_MAX_REDIRECTS = 5


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
    except Exception:
        # 无法解析的地址视为不安全
        return True
    return False


def _check_url_safety(url: str) -> str | None:
    """检查 URL 协议和 host 安全性。返回错误字符串或 None（通过）。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Error: unsupported scheme '{parsed.scheme}', only http/https allowed"
    if not parsed.hostname:
        return "Error: invalid URL, no hostname"
    if _is_private_host(parsed.hostname):
        return f"Error: access to private/local network addresses is blocked: {parsed.hostname}"
    return None


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

    # 初始 URL 安全检查
    safety_error = _check_url_safety(url)
    if safety_error:
        return safety_error

    # 使用自定义 opener 不自动跟随重定向，手动处理每次重定向并重新校验
    from urllib.request import HTTPRedirectHandler

    class _NoRedirectHandler(HTTPRedirectHandler):
        """禁止自动重定向，抛出 HTTPError 以便手动处理。"""
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise HTTPError(newurl, code, msg, headers, fp)

    opener = build_opener(_NoRedirectHandler)

    for redirect_count in range(_MAX_REDIRECTS + 1):
        try:
            req = Request(url, headers={"User-Agent": "mini-claw/0.1"})
            resp = opener.open(req, timeout=10)
            content = resp.read(max_length + 1).decode("utf-8", errors="replace")
            if len(content) > max_length:
                content = content[:max_length] + "\n... (truncated)"
            return content
        except HTTPError as e:
            # 处理重定向：提取 Location 并重新校验
            if e.code in (301, 302, 303, 307, 308):
                location = e.headers.get("Location")
                if not location:
                    return f"Error: redirect ({e.code}) with no Location header"
                url = urljoin(url, location)
                safety_error = _check_url_safety(url)
                if safety_error:
                    return safety_error
                continue
            return f"Error: HTTP {e.code}: {e.reason}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    return f"Error: too many redirects (max {_MAX_REDIRECTS})"


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
        description="Fetch the content of a web page by URL. Only http/https URLs allowed. Private/local network addresses are blocked. Redirects are validated.",
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
