"""AgentRunner wrapper：在 inner runner 执行前注入运行时上下文。

任何 AgentRunner（DeepSeek、Echo、自定义）都能通过 wrapper 获得上下文注入，
不硬编码在具体 runner 里。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from claw.ports import AgentRunner, ContextBuilder
from claw.types import AgentReply, InboundMessage, Session, StreamChunk


class ContextBuildingAgentRunner:
    """AgentRunner wrapper：在 inner runner 执行前注入运行时上下文。

    通过 skip_runtime_context 标记跳过注入（如 _full_compact 内部调用）。

    NOTE: 此 wrapper 不暴露 inner runner 的属性（如 client/model）。
    依赖 isinstance(runner, DeepSeekAgentRunner) 的逻辑必须在 wrapping 之前完成。
    """

    def __init__(self, inner: AgentRunner, context_builder: ContextBuilder) -> None:
        self._inner = inner
        self._context_builder = context_builder

    async def run(self, session: Session, message: InboundMessage) -> AgentReply:
        if not message.metadata.get("skip_runtime_context"):
            await self._context_builder.build(session, message)
        return await self._inner.run(session, message)

    async def run_stream(self, session: Session, message: InboundMessage) -> AsyncIterator[StreamChunk]:
        if not message.metadata.get("skip_runtime_context"):
            await self._context_builder.build(session, message)
        async for chunk in self._inner.run_stream(session, message):
            yield chunk
