"""端口/接口定义：用 Protocol 声明各模块的契约，实现依赖倒置。

上游模块（如 Gateway）只依赖这里的接口，不依赖具体实现。
具体实现（如 InMemorySessionStore、EchoAgentRunner）由外部注入。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from claw.types import AgentReply, InboundMessage, PlatformEvent, Session, StreamChunk


class Transport(Protocol):
    """传输层：将外部输入（CLI 文本 / webhook body）包装成 PlatformEvent。"""
    def receive(self, text: str) -> PlatformEvent: ...


class Adapter(Protocol):
    """适配器：将平台原始事件转换为标准 InboundMessage。"""
    def to_inbound_message(self, event: PlatformEvent) -> InboundMessage: ...


class Gateway(Protocol):
    """网关：接收 InboundMessage，协调会话、Agent、投递。"""
    async def handle_inbound_message(self, message: InboundMessage) -> AgentReply: ...


class DedupeStore(Protocol):
    """去重存储：防止同一个事件被处理多次。"""
    async def exists(self, key: str) -> bool: ...

    async def set(self, key: str, ttl_seconds: int | None = None) -> None: ...


class SessionStore(Protocol):
    """会话存储：按 peer_key / session_id 存取 Session，支持多会话管理。"""
    async def get(self, session_key: str) -> Session | None: ...

    async def save(self, session: Session) -> None: ...

    async def get_by_id(self, session_id: str) -> Session | None: ...

    async def delete(self, session_id: str) -> None: ...

    async def list_sessions(self, peer_key: str) -> list[Session]: ...

    async def get_active(self, peer_key: str) -> Session | None: ...

    async def set_active(self, peer_key: str, session_id: str) -> None: ...

    async def list_peer_keys(self) -> list[str]: ...


class AgentRunner(Protocol):
    """Agent 运行器：接收会话和当前消息，返回回复。"""
    async def run(self, session: Session, message: InboundMessage) -> AgentReply: ...
    async def run_stream(self, session: Session, message: InboundMessage) -> AsyncIterator[StreamChunk]: ...


class Delivery(Protocol):
    """投递：将 Agent 回复发送出去（本地记录 / API 调用等）。"""
    async def send(self, message: InboundMessage, reply: AgentReply) -> None: ...


class ContextCompressor(Protocol):
    """上下文压缩器：检测并压缩过长的会话历史。"""
    def should_compress(self, session: Session, incoming_text: str | None = None) -> bool: ...
    async def compress(self, session: Session, *, force: bool = False) -> str | None: ...


class McpProvider(Protocol):
    """MCP 提供者：管理 MCP 服务器连接和工具注册。"""
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def register_tools(self, registry: Any) -> list[str]: ...
    def get_status(self) -> list[Any]: ...
