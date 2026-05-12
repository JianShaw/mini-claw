"""MiniClaw 门面：对外保持简单的 reply(text) 接口，对内组合所有运行时模块。"""

from __future__ import annotations

import asyncio

from claw.channels.local import LocalAdapter, LocalTransport
from claw.gateway import RuntimeGateway
from claw.processor import ChannelProcessor, InMemoryDedupeStore
from claw.deepseek import DeepSeekAgentRunner
from claw.ports import AgentRunner
from claw.session import InMemorySessionStore
from claw.ports import Delivery
from claw.types import AgentReply, PlatformEvent


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
    ) -> None:
        self.transport = LocalTransport()
        self.delivery = delivery or _default_delivery()
        runner = agent_runner or DeepSeekAgentRunner(api_key=api_key)
        self.gateway = RuntimeGateway(
            session_store=InMemorySessionStore(),
            agent_runner=runner,
            delivery=self.delivery,
        )
        self.processor = ChannelProcessor(
            adapter=LocalAdapter(),
            gateway=self.gateway,
            dedupe_store=InMemoryDedupeStore(),
        )

    def reply(self, text: str) -> AgentReply:
        """同步接口，供 CLI 使用。"""
        return asyncio.run(self.areply(text))

    async def areply(self, text: str) -> AgentReply:
        """异步接口：通过 Transport 将文本转为 PlatformEvent，走完整处理链路。"""
        event: PlatformEvent = self.transport.receive(text)
        reply: AgentReply | None = await self.processor.process(event)
        return reply if reply is not None else AgentReply(text="")


def _default_delivery() -> Delivery:
    """默认使用 JsonlDelivery 将聊天记录持久化到 data/ 目录。"""
    from claw.channels.local import JsonlDelivery

    return JsonlDelivery(data_dir="data")
