"""运行时网关：回答三个核心问题——哪个会话？哪个 Agent？回复发到哪？"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from claw.ports import AgentRunner, Delivery, SessionStore
from claw.session import build_session_key, create_session
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
    ) -> None:
        self._session_store = session_store
        self._agent_runner = agent_runner
        self._delivery = delivery
        self._default_agent_id = default_agent_id

    def _peer_key(self, message: InboundMessage) -> str:
        return build_session_key(message)

    async def _get_or_create_session(self, message: InboundMessage) -> Session:
        """获取活跃 session，没有则创建并激活。"""
        peer_key = self._peer_key(message)
        session = await self._session_store.get_active(peer_key)
        if session is None:
            session = create_session(message, agent_id=self._default_agent_id)
            await self._session_store.save(session)
        return session

    async def handle_inbound_message(self, message: InboundMessage) -> AgentReply:
        session = await self._get_or_create_session(message)

        # AgentRunner 会往 session.history 追加记录
        reply = await self._agent_runner.run(session, message)

        message.metadata["session_id"] = session.session_id
        await self._session_store.save(session)
        await self._delivery.send(message, reply)

        return reply

    async def handle_stream(self, message: InboundMessage) -> AsyncIterator[StreamChunk]:
        """流式处理：yield StreamChunk，流结束后保存完整 assistant message 并投递。
        thinking 内容不写入 history，但包含在 Delivery 的 AgentReply.metadata 中。"""
        session = await self._get_or_create_session(message)

        full_text = ""
        full_thinking = ""
        async for chunk in self._agent_runner.run_stream(session, message):
            if chunk.type == "thinking":
                full_thinking += chunk.text
            else:
                full_text += chunk.text
            yield chunk

        # 流结束，保存完整 assistant message（仅 content 部分）
        session.history.append(ChatMessage(role="assistant", content=full_text))
        message.metadata["session_id"] = session.session_id
        await self._session_store.save(session)
        metadata: dict[str, Any] = {}
        if full_thinking:
            metadata["reasoning"] = full_thinking
        await self._delivery.send(message, AgentReply(text=full_text, metadata=metadata))

    # --- 会话管理方法 ---

    async def create_new_session(self, message: InboundMessage) -> Session:
        """创建新 session 并激活。"""
        session = create_session(message, agent_id=self._default_agent_id)
        await self._session_store.save(session)
        await self._session_store.set_active(
            self._peer_key(message), session.session_id
        )
        return session

    async def list_sessions(self, message: InboundMessage) -> list[Session]:
        """列出 peer 下的所有 session。"""
        return await self._session_store.list_sessions(self._peer_key(message))

    async def select_session(
        self, message: InboundMessage, session_id: str
    ) -> Session | None:
        """切换活跃 session，返回切换后的 session。仅允许切换同 peer 下的 session。"""
        session = await self._session_store.get_by_id(session_id)
        if session is None:
            return None
        # 归属校验：session 必须属于当前 peer
        if session.session_key != self._peer_key(message):
            return None
        await self._session_store.set_active(
            self._peer_key(message), session_id
        )
        return session

    async def delete_session(
        self, message: InboundMessage, session_id: str
    ) -> None:
        """删除指定 session。"""
        await self._session_store.delete(session_id)

    async def compact_session(self, message: InboundMessage) -> str | None:
        """压缩当前活跃 session 的上下文：调用 LLM 生成摘要，清空 history。

        返回生成的摘要文本，如果 session 为空或不存在则返回 None。
        原始 JSONL 记录不会被删除（由 JsonlSessionStore 保证）。
        """
        peer_key = self._peer_key(message)
        session = await self._session_store.get_active(peer_key)
        if session is None or not session.history:
            return None

        # 构建摘要 prompt
        history_text = "\n".join(
            f"{m.role}: {m.content}" for m in session.history
        )
        existing = f"已有摘要：\n{session.summary}\n\n" if session.summary else ""
        prompt = (
            f"{existing}请总结以下对话的关键信息，保留重要的事实和上下文：\n\n"
            f"{history_text}"
        )

        # 用临时 session 调用 AgentRunner 生成摘要
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
        )
        reply = await self._agent_runner.run(temp_session, temp_msg)

        # 更新 session：设置摘要，清空 history
        session.summary = reply.text
        session.history = []
        await self._session_store.save(session)

        return reply.text
