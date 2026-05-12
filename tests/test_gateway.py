"""Gateway 测试：验证会话创建/复用、Agent 调度、回复投递。"""

from __future__ import annotations

from claw.channels.local import LocalDelivery
from claw.gateway import RuntimeGateway
from claw.runner import EchoAgentRunner
from claw.session import InMemorySessionStore, create_session
from claw.types import AgentReply, ChatMessage, InboundMessage


def _msg(text: str = "hello") -> InboundMessage:
    """构造 InboundMessage 的辅助函数。"""
    return InboundMessage(
        channel="local",
        account_id="app",
        peer_id="user",
        sender_id="user",
        message_id="1",
        text=text,
        timestamp=0,
        message_type="text",
        raw=None,
    )


async def test_gateway_creates_session_when_missing() -> None:
    """首次消息应自动创建 Session 并保存到 SessionStore。"""
    store = InMemorySessionStore()
    delivery = LocalDelivery()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=EchoAgentRunner(),
        delivery=delivery,
    )
    reply = await gateway.handle_inbound_message(_msg())
    assert reply.text == "echo: hello"
    session = await store.get("local:app:user")
    assert session is not None
    assert len(session.history) == 2


async def test_gateway_reuses_existing_session() -> None:
    """同一 session_key 的多次消息应复用同一个 Session，history 累积。"""
    store = InMemorySessionStore()
    existing = create_session(_msg())
    await store.save(existing)
    delivery = LocalDelivery()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=EchoAgentRunner(),
        delivery=delivery,
    )
    await gateway.handle_inbound_message(_msg("first"))
    await gateway.handle_inbound_message(_msg("second"))
    session = await store.get("local:app:user")
    assert len(session.history) == 4  # 2 messages x 2 entries each


async def test_gateway_sends_reply_through_delivery() -> None:
    """回复应通过 Delivery.send() 投递。"""
    delivery = LocalDelivery()
    gateway = RuntimeGateway(
        session_store=InMemorySessionStore(),
        agent_runner=EchoAgentRunner(),
        delivery=delivery,
    )
    msg = _msg()
    await gateway.handle_inbound_message(msg)
    assert len(delivery.sent) == 1
    assert delivery.sent[0][1].text == "echo: hello"


async def test_gateway_returns_agent_reply() -> None:
    """handle_inbound_message 应返回 AgentReply。"""
    gateway = RuntimeGateway(
        session_store=InMemorySessionStore(),
        agent_runner=EchoAgentRunner(),
        delivery=LocalDelivery(),
    )
    reply = await gateway.handle_inbound_message(_msg("test"))
    assert reply.text == "echo: test"
