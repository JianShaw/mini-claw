"""Agent 运行器：当前为 Echo 实现，验证整条链路可用。后续可替换为真实 LLM 调用。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from time import time

from claw.types import AgentReply, ChatMessage, InboundMessage, Session, StreamChunk


class EchoAgentRunner:
    """回声 Agent：将用户输入原样返回，同时维护对话历史。

    用于在不需要网络/LLM 的情况下验证整个运行时链路。
    """

    async def run(self, session: Session, message: InboundMessage) -> AgentReply:
        session.history.append(ChatMessage(role="user", content=message.text, ts=int(time() * 1000)))
        reply = AgentReply(text=f"echo: {message.text}")
        session.history.append(ChatMessage(role="assistant", content=reply.text, ts=int(time() * 1000)))
        return reply

    async def run_stream(self, session: Session, message: InboundMessage) -> AsyncIterator[StreamChunk]:
        """流式版本：yield StreamChunk。assistant history 由调用方（Gateway）追加。"""
        session.history.append(ChatMessage(role="user", content=message.text, ts=int(time() * 1000)))
        yield StreamChunk(type="content", text=f"echo: {message.text}")
