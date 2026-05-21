"""运行时网关：回答三个核心问题——哪个会话？哪个 Agent？回复发到哪？"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

from claw.ports import AgentRunner, ContextCompressor, Delivery, SessionStore
from claw.session import build_peer_key, create_session, create_session_from_identity
from claw.types import AgentReply, ChatMessage, InboundMessage, Session, StreamChunk


class RuntimeGateway:
    """核心编排模块，协调会话查找/创建、Agent 执行、回复投递。

    依赖通过构造函数注入（SessionStore / AgentRunner / Delivery），
    不直接 import 任何具体实现。
    """

    def __init__(
        self,
        session_store: SessionStore,
        agent_runner: AgentRunner,
        delivery: Delivery,
        default_agent_id: str = "default-agent",
        compressor: ContextCompressor | None = None,
        memory_manager: Any | None = None,
        agent_resolver: Any | None = None,
    ) -> None:
        self._session_store = session_store
        self._agent_runner = agent_runner
        self._delivery = delivery
        self._default_agent_id = default_agent_id
        self._compressor = compressor
        self._memory_manager = memory_manager
        self._agent_resolver = agent_resolver

    # -- 只读属性：暴露内部组件供 AgentRunService 等外部模块复用 --

    @property
    def agent_runner(self) -> AgentRunner:
        return self._agent_runner

    @property
    def session_store(self) -> SessionStore:
        return self._session_store

    @property
    def memory_manager(self) -> Any | None:
        return self._memory_manager

    @property
    def compressor(self) -> ContextCompressor | None:
        return self._compressor

    @property
    def agent_resolver(self) -> Any | None:
        return self._agent_resolver

    def _peer_key(self, message: InboundMessage) -> str:
        return build_peer_key(message.channel, message.account_id, message.peer_id)

    async def _get_or_create_session(self, message: InboundMessage) -> Session:
        """获取活跃 session，没有则创建并激活。"""
        peer_key = self._peer_key(message)
        session = await self._session_store.get_active(peer_key)
        if session is None:
            session = create_session(message, agent_id=self._default_agent_id)
            await self._session_store.save(session)
        return session

    async def _auto_compress_if_needed(
        self, session: Session, incoming_text: str
    ) -> str | None:
        """检查并执行自动压缩，成功后立即持久化 session，返回摘要或 None。"""
        if self._compressor is None:
            return None
        if not self._compressor.should_compress(session, incoming_text=incoming_text):
            return None
        summary = await self._compressor.compress(session)
        if summary is not None:
            await self._session_store.save(session)
        return summary

    async def _inject_agent_runtime_profile(self, session: Session) -> None:
        """按 session.agent_id 解析本轮运行配置并注入 session metadata。"""
        if self._agent_resolver is None:
            session.metadata.pop("agent_runtime_profile", None)
            return
        profile = self._agent_resolver.resolve(session.agent_id)
        # RuntimeProfile 是 dataclass，需转 dict 才能 JSON 序列化到 SQLite
        from dataclasses import asdict
        session.metadata["agent_runtime_profile"] = asdict(profile)

    async def _resolve_session(self, message: InboundMessage) -> Session:
        """解析消息对应的 session，支持显式 session_id 路由。

        Web 端通过 message.metadata["session_id"] 指定目标 session，
        避免多对话/多标签页串到同一个 active session。
        无 session_id 时按 peer active session 兼容 CLI。
        """
        session_id = message.metadata.get("session_id")
        if session_id:
            session = await self._session_store.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError(session_id)
            return session
        return await self._get_or_create_session(message)

    async def _maybe_update_daily_memory(self, session: Session, *, force: bool = False) -> None:
        """按策略更新 daily memory；无 memory manager 时保持空操作。"""
        if self._memory_manager is None:
            return
        await self._memory_manager.maybe_update_daily(session, force=force)

    async def _distill_memory(self) -> None:
        """compact 后把 daily memory 的长期候选合并进 MEMORY.md。"""
        if self._memory_manager is None:
            return
        await self._memory_manager.distill_daily_to_long_term()

    async def handle_inbound_message(self, message: InboundMessage) -> AgentReply:
        session = await self._resolve_session(message)

        # 自动压缩：在调 runner 之前检查并执行，成功后立即持久化
        compact_summary = await self._auto_compress_if_needed(session, message.text)
        await self._inject_agent_runtime_profile(session)

        # AgentRunner（或 wrapper）负责注入 memory/skill 上下文
        reply = await self._agent_runner.run(session, message)

        # 标记自动压缩事件，供上层（chat app）感知
        if compact_summary is not None:
            reply.metadata["auto_compact"] = True
            reply.metadata["compact_summary"] = compact_summary

        message.metadata["session_id"] = session.session_id
        await self._maybe_update_daily_memory(session)
        await self._session_store.save(session)
        await self._delivery.send(message, reply)

        return reply

    async def handle_stream(self, message: InboundMessage) -> AsyncIterator[StreamChunk]:
        """流式处理：yield StreamChunk，流结束后保存完整 assistant message 并投递。
        thinking 内容不写入 history，但包含在 Delivery 的 AgentReply.metadata 中。
        自动压缩事件通过 StreamChunk(type="system") 通知。"""
        session = await self._resolve_session(message)

        # 自动压缩：在调 runner 之前检查并执行
        compact_summary = await self._auto_compress_if_needed(session, message.text)
        if compact_summary is not None:
            yield StreamChunk(type="system", text=f"[auto-compact] {compact_summary}")
        await self._inject_agent_runtime_profile(session)

        full_text = ""
        full_thinking = ""
        async for chunk in self._agent_runner.run_stream(session, message):
            if chunk.type == "thinking":
                full_thinking += chunk.text
            elif chunk.type == "content":
                full_text += chunk.text
            # tool_call / tool_result / system 类型的 chunk 仅作为 UI 事件透传，不聚合到回复文本
            yield chunk

        # 流结束，保存完整 assistant message（仅 content 部分）
        session.history.append(ChatMessage(role="assistant", content=full_text))
        message.metadata["session_id"] = session.session_id
        await self._maybe_update_daily_memory(session)
        logger.info(
            "handle_stream saving session=%s history=%d msgs",
            session.session_id, len(session.history),
        )
        await self._session_store.save(session)
        logger.info("handle_stream saved OK session=%s", session.session_id)
        metadata: dict[str, Any] = {}
        if full_thinking:
            metadata["reasoning"] = full_thinking
        await self._delivery.send(message, AgentReply(text=full_text, metadata=metadata))

    # --- 会话管理方法（接受 peer_key，不依赖 InboundMessage） ---

    async def create_new_session(
        self,
        peer_key: str,
        *,
        channel: str,
        account_id: str,
        peer_id: str,
        sender_id: str,
        agent_id: str | None = None,
    ) -> Session:
        """创建新 session 并激活。"""
        session = create_session_from_identity(
            channel=channel,
            account_id=account_id,
            peer_id=peer_id,
            sender_id=sender_id,
            agent_id=agent_id or self._default_agent_id,
        )
        await self._session_store.save(session)
        await self._session_store.set_active(peer_key, session.session_id)
        return session

    async def list_sessions(self, peer_key: str) -> list[Session]:
        """列出 peer 下的所有 session。"""
        return await self._session_store.list_sessions(peer_key)

    async def select_session(
        self, peer_key: str, session_id: str
    ) -> Session | None:
        """切换活跃 session，返回切换后的 session。仅允许切换同 peer 下的 session。"""
        session = await self._session_store.get_by_id(session_id)
        if session is None:
            return None
        # 归属校验：session 必须属于当前 peer
        if session.session_key != peer_key:
            return None
        await self._session_store.set_active(peer_key, session_id)
        return session

    async def delete_session(self, peer_key: str, session_id: str) -> None:
        """删除指定 session。"""
        await self._session_store.delete(session_id)

    async def compact_session(self, peer_key: str) -> str | None:
        """压缩当前活跃 session 的上下文。

        有 compressor 时使用 force=True 保留最近 N 轮；
        无 compressor 时 fallback 到全量压缩（清空 history）。
        两种路径都正确设置 history_offset。
        """
        session = await self._session_store.get_active(peer_key)
        if session is None:
            return None
        if not session.history:
            return ""
        await self._maybe_update_daily_memory(session, force=True)

        # 有 compressor：使用 force=True，保留最近 N 轮
        if self._compressor is not None:
            summary = await self._compressor.compress(session, force=True)
            if summary is not None:
                await self._session_store.save(session)
                await self._distill_memory()
                return summary
            # 消息轮数不足 keep_rounds，无法压缩
            return ""

        # Fallback：全量压缩（兼容无 compressor 的测试 runner）
        summary = await self._full_compact(session)
        if summary is not None:
            await self._distill_memory()
        return summary

    async def _full_compact(self, session: Session) -> str | None:
        """全量压缩 fallback：清空 history，正确设置 history_offset。"""
        history_text = "\n".join(
            f"{m.role}: {m.content}" for m in session.history
        )
        existing = f"已有摘要：\n{session.summary}\n\n" if session.summary else ""
        prompt = (
            f"{existing}请总结以下对话的关键信息，保留重要的事实和上下文：\n\n"
            f"{history_text}"
        )

        # 用临时 session 调 AgentRunner 生成摘要
        temp_session = Session(
            session_id="temp",
            session_key="temp",
            channel=session.channel,
            account_id=session.account_id,
            peer_id=session.peer_id,
            sender_id=session.sender_id,
            agent_id=session.agent_id,
        )
        temp_msg = InboundMessage(
            channel=session.channel,
            account_id=session.account_id,
            peer_id=session.peer_id,
            sender_id=session.sender_id,
            message_id="compact",
            text=prompt,
            timestamp=0,
            message_type="text",
            raw=None,
            metadata={"skip_runtime_context": True},
        )
        reply = await self._agent_runner.run(temp_session, temp_msg)

        # 全量清空 history，正确设置 offset
        session.history_offset += len(session.history)
        session.summary = reply.text
        session.history = []
        await self._session_store.save(session)

        return reply.text

    # --- Agent 绑定的会话创建 ---

    async def create_session_for_agent(
        self,
        peer_key: str,
        agent_id: str,
        *,
        channel: str,
        account_id: str,
        peer_id: str,
        sender_id: str,
    ) -> Session:
        """创建绑定指定 agent_id 的 session 并激活。"""
        session = create_session_from_identity(
            channel=channel,
            account_id=account_id,
            peer_id=peer_id,
            sender_id=sender_id,
            agent_id=agent_id,
        )
        await self._session_store.save(session)
        await self._session_store.set_active(peer_key, session.session_id)
        return session

    async def get_session_by_id(self, session_id: str) -> Session | None:
        """按 session_id 获取 session。"""
        return await self._session_store.get_by_id(session_id)


class SessionNotFoundError(Exception):
    """显式 session_id 找不到对应 session 时抛出。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")
