"""MiniClaw 门面：对外保持简单的 reply(text) 接口，对内组合所有运行时模块。"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from claw.channels.local import LocalAdapter, LocalTransport
from claw.gateway import RuntimeGateway
from claw.processor import ChannelProcessor, InMemoryDedupeStore
from claw.deepseek import DeepSeekAgentRunner
from claw.ports import AgentRunner, SessionStore
from claw.session import JsonlSessionStore
from claw.ports import Delivery
from claw.tools import ToolsRegistry
from claw.types import AgentReply, InboundMessage, PlatformEvent, Session, StreamChunk

logger = logging.getLogger(__name__)


def _env_int(key: str, default: int) -> int:
    """从环境变量读取整数，无效值时返回默认值。"""
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


class MiniClaw:
    """组合根：在这里组装所有依赖关系，外部只需调用 reply()。

    数据流：text → Transport.receive() → PlatformEvent
      → Processor(去重/适配/校验/过滤)
        → Gateway(会话/Agent/投递)
          → Delivery.send()
    """

    def __init__(
        self,
        delivery: Delivery | None = None,
        *,
        agent_runner: AgentRunner | None = None,
        api_key: str | None = None,
        session_store: SessionStore | None = None,
        auto_compact: bool = True,
        max_tokens: int | None = None,
        keep_rounds: int | None = None,
        tools_registry: ToolsRegistry | None = None,
        mcp_config_path: str | None = None,
    ) -> None:
        self.transport = LocalTransport()
        self.delivery = delivery or _default_delivery()
        runner = agent_runner or DeepSeekAgentRunner(api_key=api_key, tools_registry=tools_registry)
        self._session_store = session_store or JsonlSessionStore()

        # 环境变量优先，参数次之，最后用默认值
        resolved_max_tokens = _env_int("COMPACT_MAX_TOKENS", max_tokens or 8000)
        resolved_keep_rounds = _env_int("COMPACT_KEEP_ROUNDS", keep_rounds or 4)

        # 自动压缩配置：compressor 注入到 Gateway
        compressor = None
        if auto_compact and isinstance(runner, DeepSeekAgentRunner):
            from claw.compressor import ContextCompressor
            compressor = ContextCompressor(
                client=runner.client,
                model=runner.model,
                max_tokens=resolved_max_tokens,
                keep_rounds=resolved_keep_rounds,
            )

        self.gateway = RuntimeGateway(
            session_store=self._session_store,
            agent_runner=runner,
            delivery=self.delivery,
            compressor=compressor,
        )
        self.processor = ChannelProcessor(
            adapter=LocalAdapter(),
            gateway=self.gateway,
            dedupe_store=InMemoryDedupeStore(),
        )

        # MCP 集成：可选的外部工具源
        self._mcp_config_path = mcp_config_path
        self._mcp_manager: Any = None  # McpManager，延迟初始化
        self._tools_registry = tools_registry

    def _routing_message(self, text: str = "") -> InboundMessage:
        """构造一条 InboundMessage 用于获取路由字段（channel/account_id/peer_id）。"""
        event: PlatformEvent = self.transport.receive(text or "_")
        return LocalAdapter().to_inbound_message(event)

    def reply(self, text: str) -> AgentReply:
        """同步接口，供 CLI 使用。"""
        return asyncio.run(self.areply(text))

    async def areply(self, text: str) -> AgentReply:
        """异步接口：通过 Transport 将文本转为 PlatformEvent，走完整处理链路。"""
        event: PlatformEvent = self.transport.receive(text)
        reply: AgentReply | None = await self.processor.process(event)
        return reply if reply is not None else AgentReply(text="")

    async def areply_stream(self, text: str) -> AsyncIterator[StreamChunk]:
        """流式异步接口：通过流式链路逐步返回 StreamChunk。"""
        event: PlatformEvent = self.transport.receive(text)
        async for chunk in self.processor.process_stream(event):
            yield chunk

    # --- 会话管理便捷方法 ---

    async def new_session(self) -> Session:
        """创建新会话并激活。"""
        msg = self._routing_message()
        return await self.gateway.create_new_session(msg)

    async def list_sessions(self) -> list[Session]:
        """列出当前 peer 下的所有会话。"""
        msg = self._routing_message()
        return await self.gateway.list_sessions(msg)

    async def select_session(self, session_id: str) -> Session | None:
        """切换到指定会话。"""
        msg = self._routing_message()
        return await self.gateway.select_session(msg, session_id)

    async def delete_session(self, session_id: str) -> None:
        """删除指定会话。"""
        msg = self._routing_message()
        return await self.gateway.delete_session(msg, session_id)

    async def compact_session(self) -> str | None:
        """压缩当前会话的上下文，返回生成的摘要。"""
        msg = self._routing_message()
        return await self.gateway.compact_session(msg)

    async def get_active_session_id(self) -> str | None:
        """获取当前活跃会话的 ID。"""
        msg = self._routing_message()
        session = await self._session_store.get_active(
            f"{msg.channel}:{msg.account_id}:{msg.peer_id}"
        )
        return session.session_id if session else None

    # --- MCP 生命周期管理 ---

    async def start(self) -> None:
        """启动 MCP 连接并注册工具。无配置时为空操作。失败时清理并记录错误。"""
        if not self._mcp_config_path:
            return
        try:
            from claw.mcp.manager import McpManager
            self._mcp_manager = McpManager.from_config_file(self._mcp_config_path)
            await self._mcp_manager.start()
            # 如果有工具注册表，桥接 MCP 工具
            if self._tools_registry is not None:
                self._mcp_manager.register_tools(self._tools_registry)
        except Exception as exc:
            # 启动失败时清理资源
            if self._mcp_manager is not None:
                try:
                    await self._mcp_manager.stop()
                except Exception:
                    pass
                self._mcp_manager = None
            logger.error("Failed to start MCP manager: %s", exc)

    async def stop(self) -> None:
        """停止 MCP 连接，释放资源。"""
        if self._mcp_manager is not None:
            await self._mcp_manager.stop()
            self._mcp_manager = None

    def get_mcp_status(self) -> list[Any]:
        """返回 MCP 服务器状态列表。"""
        if self._mcp_manager is None:
            return []
        return self._mcp_manager.get_status()


def _default_delivery() -> Delivery:
    """默认使用 LocalDelivery，会话持久化由 JsonlSessionStore 接管。"""
    from claw.channels.local import LocalDelivery

    return LocalDelivery()
