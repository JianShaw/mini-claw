"""MCP 服务器连接：管理单个 MCP 服务器的连接生命周期。

支持三种传输方式：
- stdio: 通过子进程 stdin/stdout 通信
- sse: 通过 Server-Sent Events 通信
- streamable-http: 通过 HTTP 流式通信

连接架构：在独立的后台 Task 中持有所有 async context manager（transport +
ClientSession），所有 session 操作通过 request/response 队列路由到后台 Task 内执行，
确保 anyio TaskGroup 内的 stream 操作不会跨 task。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from claw.mcp.types import (
    McpPromptInfo,
    McpResourceInfo,
    McpServerConfig,
)

logger = logging.getLogger(__name__)

# 后台 Task 处理请求的类型常量
_REQ_CALL_TOOL = "call_tool"
_REQ_READ_RESOURCE = "read_resource"
_REQ_GET_PROMPT = "get_prompt"


class McpServerConnection:
    """单个 MCP 服务器的连接管理器。

    在后台 Task 中运行 MCP transport 和 ClientSession。
    所有 session 操作（call_tool / read_resource / get_prompt）通过
    asyncio.Queue 路由到后台 Task 内执行，避免 anyio 跨 task 问题。
    """

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self.name = config.name
        self._tools: list[Any] = []
        self._resources: list[McpResourceInfo] = []
        self._prompts: list[McpPromptInfo] = []
        self._connected = False
        self._error: str | None = None
        self._background_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._ready_event: asyncio.Event = asyncio.Event()
        self._request_queue: asyncio.Queue[tuple[str, str, Any]] = asyncio.Queue()
        # pending 请求：request_id → asyncio.Future
        self._pending: dict[str, asyncio.Future[str]] = {}

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tools(self) -> list[Any]:
        """返回发现的 MCP 工具原始对象列表。"""
        return list(self._tools)

    @property
    def resources(self) -> list[McpResourceInfo]:
        return list(self._resources)

    @property
    def prompts(self) -> list[McpPromptInfo]:
        return list(self._prompts)

    @property
    def error(self) -> str | None:
        return self._error

    async def connect(self) -> None:
        """建立连接：启动后台 Task 并等待连接就绪或失败。"""
        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._request_queue = asyncio.Queue()
        self._pending = {}
        self._connected = False
        self._error = None

        self._background_task = asyncio.create_task(
            self._run(), name=f"mcp-{self.name}"
        )

        # 等待后台 Task 通知就绪（或出错）
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            self._error = "Connection timeout (30s)"
            self._connected = False
            logger.warning("MCP server '%s' connection timeout", self.name)
            self._cancel_task()

        if not self._connected:
            if self._background_task is not None:
                try:
                    await self._background_task
                except Exception:
                    pass

    async def _run(self) -> None:
        """后台 Task：持有所有 context manager，处理请求队列。"""
        try:
            async with self._create_transport() as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    await self._discover(session)
                    self._connected = True
                    self._ready_event.set()

                    logger.info(
                        "MCP server '%s' connected: %d tools, %d resources, %d prompts",
                        self.name,
                        len(self._tools),
                        len(self._resources),
                        len(self._prompts),
                    )

                    # 进入请求处理循环
                    await self._serve_requests(session)
        except Exception as exc:
            self._error = str(exc)
            self._connected = False
            logger.warning("MCP server '%s' connection failed: %s", self.name, exc)
            self._ready_event.set()
            # 拒绝所有等待中的请求
            self._reject_pending(exc)

    async def _serve_requests(self, session: ClientSession) -> None:
        """在后台 Task 内循环处理请求，直到收到停止信号。"""
        while not self._stop_event.is_set():
            try:
                # 用短超时轮询，以便及时响应 stop_event
                req = await asyncio.wait_for(
                    self._request_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue

            req_type, req_id, args = req
            future = self._pending.pop(req_id, None)
            if future is None or future.done():
                continue

            try:
                result = await self._handle_request(session, req_type, args)
                if not future.done():
                    future.set_result(result)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)

    async def _handle_request(
        self, session: ClientSession, req_type: str, args: Any
    ) -> str:
        """在后台 Task 内执行单个请求。"""
        if req_type == _REQ_CALL_TOOL:
            name, arguments = args
            result = await session.call_tool(name, arguments)
            return _extract_text(result.content)

        elif req_type == _REQ_READ_RESOURCE:
            uri = args
            result = await session.read_resource(uri)  # type: ignore[arg-type]
            return _extract_text(result.contents)

        elif req_type == _REQ_GET_PROMPT:
            name, arguments = args
            result = await session.get_prompt(name, arguments)
            parts: list[str] = []
            for msg in result.messages:
                if hasattr(msg, "content") and hasattr(msg.content, "text"):
                    parts.append(msg.content.text)
                else:
                    parts.append(str(msg))
            return "\n".join(parts) if parts else ""

        return f"[MCP error: unknown request type: {req_type}]"

    async def _submit_request(
        self, req_type: str, args: Any, timeout: float = 60.0
    ) -> str:
        """提交请求到后台 Task 执行，等待结果。"""
        if not self._connected:
            return f"[MCP error: server '{self.name}' not connected]"

        req_id = uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[req_id] = future

        await self._request_queue.put((req_type, req_id, args))

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return f"[MCP error: tool call to '{self.name}' timed out ({timeout}s)]"
        except Exception as exc:
            return f"[MCP tool error: {type(exc).__name__}: {exc}]"

    def _reject_pending(self, exc: Exception) -> None:
        """连接断开时拒绝所有等待中的请求。"""
        for req_id, future in self._pending.items():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    def _create_transport(self) -> Any:
        """根据配置创建对应的 transport async context manager。"""
        transport = self.config.transport
        if transport == "stdio":
            return stdio_client(StdioServerParameters(
                command=self.config.command or "",
                args=self.config.args,
                env=self.config.env or None,
            ))
        elif transport == "sse":
            from mcp.client.sse import sse_client
            return sse_client(
                url=self.config.url or "",
                headers=self.config.headers or None,
            )
        elif transport == "streamable-http":
            from mcp.client.streamable_http import streamable_http_client
            return streamable_http_client(url=self.config.url or "")
        else:
            raise ValueError(f"Unsupported transport: {transport}")

    async def _discover(self, session: ClientSession) -> None:
        """发现并缓存服务器提供的工具、资源和提示。"""
        # 发现工具
        try:
            tools_result = await session.list_tools()
            self._tools = list(tools_result.tools)
        except Exception as exc:
            logger.warning("Failed to list tools from '%s': %s", self.name, exc)
            self._tools = []

        # 发现资源
        try:
            resources_result = await session.list_resources()
            self._resources = [
                McpResourceInfo(
                    server_name=self.name,
                    uri=str(r.uri),
                    name=r.name,
                    description=getattr(r, "description", None),
                    mime_type=getattr(r, "mime_type", None) or getattr(r, "mimeType", None),
                )
                for r in resources_result.resources
            ]
        except Exception as exc:
            logger.debug("No resources from '%s': %s", self.name, exc)
            self._resources = []

        # 发现提示
        try:
            prompts_result = await session.list_prompts()
            self._prompts = [
                McpPromptInfo(
                    server_name=self.name,
                    name=p.name,
                    description=getattr(p, "description", None),
                    arguments=[
                        {"name": a.name, "description": getattr(a, "description", None), "required": getattr(a, "required", False)}
                        for a in (p.arguments or [])
                    ],
                )
                for p in prompts_result.prompts
            ]
        except Exception as exc:
            logger.debug("No prompts from '%s': %s", self.name, exc)
            self._prompts = []

    # --- 外部调用接口（通过队列路由到后台 Task） ---

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """调用 MCP 工具并返回结果文本。通过后台 Task 执行。"""
        return await self._submit_request(_REQ_CALL_TOOL, (name, arguments))

    async def read_resource(self, uri: str) -> str:
        """读取 MCP 资源并返回内容文本。通过后台 Task 执行。"""
        return await self._submit_request(_REQ_READ_RESOURCE, uri)

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> str:
        """获取 MCP 提示模板并返回内容文本。通过后台 Task 执行。"""
        return await self._submit_request(_REQ_GET_PROMPT, (name, arguments))

    def _cancel_task(self) -> None:
        """取消后台 Task。"""
        if self._background_task is not None and not self._background_task.done():
            self._background_task.cancel()

    async def disconnect(self) -> None:
        """优雅关闭连接，释放所有资源。"""
        self._connected = False
        self._tools = []
        self._resources = []
        self._prompts = []

        # 通知后台 Task 停止请求循环
        self._stop_event.set()

        # 拒绝所有等待中的请求
        for req_id, future in self._pending.items():
            if not future.done():
                future.set_exception(ConnectionError("disconnecting"))
        self._pending.clear()

        if self._background_task is not None:
            try:
                await asyncio.wait_for(self._background_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._cancel_task()
            self._background_task = None

        logger.info("MCP server '%s' disconnected", self.name)


def _extract_text(items: Any) -> str:
    """从 MCP 结果的 content/contents 列表中提取文本。"""
    parts: list[str] = []
    for item in items:
        if hasattr(item, "text"):
            parts.append(item.text)
        else:
            parts.append(str(item))
    return "\n".join(parts) if parts else ""
