"""ChannelProcessor 测试：去重、元数据补充、校验、过滤、异常兜底。"""

from __future__ import annotations

from typing import Any

from claw.channels.local import LocalAdapter, LocalDelivery
from claw.gateway import RuntimeGateway
from claw.processor import ChannelProcessor, InMemoryDedupeStore
from claw.runner import EchoAgentRunner
from claw.session import InMemorySessionStore
from claw.types import AgentReply, InboundMessage, PlatformEvent


def _event(event_id: str = "e1", **payload_overrides: Any) -> PlatformEvent:
    """构造 PlatformEvent 的辅助函数，支持覆盖 payload 字段。"""
    payload: dict[str, Any] = {
        "peer_id": "user",
        "sender_id": "user",
        "message_id": "m1",
        "text": "hello",
    }
    payload.update(payload_overrides)
    return PlatformEvent(
        platform="local",
        transport="cli",
        event_id=event_id,
        received_at=0,
        payload=payload,
    )


def _processor() -> ChannelProcessor:
    """构造一个使用本地内存实现的完整 Processor。"""
    return ChannelProcessor(
        adapter=LocalAdapter(),
        gateway=RuntimeGateway(
            session_store=InMemorySessionStore(),
            agent_runner=EchoAgentRunner(),
            delivery=LocalDelivery(),
        ),
        dedupe_store=InMemoryDedupeStore(),
    )


async def test_processor_dedupes_by_platform_and_event_id() -> None:
    """相同 platform + event_id 的事件只处理一次，第二次返回 None。"""
    proc = _processor()
    r1 = await proc.process(_event("e1"))
    r2 = await proc.process(_event("e1"))
    assert r1 is not None
    assert r2 is None


async def test_processor_adds_event_metadata_to_inbound_message() -> None:
    """Processor 应将 transport、event_id、received_at 补充到 InboundMessage.metadata。"""
    delivery = LocalDelivery()
    proc = ChannelProcessor(
        adapter=LocalAdapter(),
        gateway=RuntimeGateway(
            session_store=InMemorySessionStore(),
            agent_runner=EchoAgentRunner(),
            delivery=delivery,
        ),
        dedupe_store=InMemoryDedupeStore(),
    )
    await proc.process(_event())
    msg, _ = delivery.sent[0]
    assert msg.metadata["transport"] == "cli"
    assert msg.metadata["event_id"] == "e1"


async def test_processor_rejects_empty_text_message() -> None:
    """纯空格的文本消息应被校验拒绝，返回 None。"""
    proc = _processor()
    reply = await proc.process(_event(text="   "))
    assert reply is None


async def test_processor_ignores_bot_messages() -> None:
    """metadata 中 is_from_bot=True 的消息应被忽略。"""
    proc = _processor()
    reply = await proc.process(_event(metadata={"is_from_bot": True}))
    assert reply is None


async def test_processor_ignores_system_events() -> None:
    """metadata 中 event_type=system 的消息应被忽略。"""
    proc = _processor()
    reply = await proc.process(_event(metadata={"event_type": "system"}))
    assert reply is None


async def test_processor_calls_gateway_for_valid_message() -> None:
    """合法消息应成功交给 Gateway 处理并返回回复。"""
    proc = _processor()
    reply = await proc.process(_event())
    assert reply is not None
    assert reply.text == "echo: hello"


async def test_processor_catches_adapter_errors() -> None:
    """Adapter 抛异常时，Processor 应静默返回 None，不向上传播。"""
    class BrokenAdapter:
        def to_inbound_message(self, event: PlatformEvent) -> InboundMessage:
            raise RuntimeError("broken")

    proc = ChannelProcessor(
        adapter=BrokenAdapter(),
        gateway=RuntimeGateway(
            session_store=InMemorySessionStore(),
            agent_runner=EchoAgentRunner(),
            delivery=LocalDelivery(),
        ),
        dedupe_store=InMemoryDedupeStore(),
    )
    reply = await proc.process(_event())
    assert reply is None


async def test_processor_catches_gateway_errors() -> None:
    """Gateway 抛异常时，Processor 应静默返回 None，不向上传播。"""
    class BrokenGateway:
        async def handle_inbound_message(self, message: InboundMessage) -> AgentReply:
            raise RuntimeError("broken")

    proc = ChannelProcessor(
        adapter=LocalAdapter(),
        gateway=BrokenGateway(),
        dedupe_store=InMemoryDedupeStore(),
    )
    reply = await proc.process(_event())
    assert reply is None
