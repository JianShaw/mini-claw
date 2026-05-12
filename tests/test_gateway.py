"""Gateway 测试：验证会话创建/复用、Agent 调度、回复投递。"""

from __future__ import annotations

from claw.channels.local import LocalDelivery
from claw.gateway import RuntimeGateway
from claw.runner import EchoAgentRunner
from claw.session import InMemorySessionStore, create_session
from claw.types import AgentReply, ChatMessage, InboundMessage, StreamChunk


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


# --- handle_stream 测试 ---


async def test_gateway_stream_yields_chunks() -> None:
    """handle_stream 应 yield StreamChunk。"""
    gateway = RuntimeGateway(
        session_store=InMemorySessionStore(),
        agent_runner=EchoAgentRunner(),
        delivery=LocalDelivery(),
    )
    chunks: list[StreamChunk] = []
    async for chunk in gateway.handle_stream(_msg("hello")):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].type == "content"
    assert chunks[0].text == "echo: hello"


async def test_gateway_stream_saves_complete_assistant_to_history() -> None:
    """流结束后完整 assistant message 应写入 history。"""
    store = InMemorySessionStore()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=EchoAgentRunner(),
        delivery=LocalDelivery(),
    )
    async for _ in gateway.handle_stream(_msg("hello")):
        pass
    session = await store.get("local:app:user")
    assert session is not None
    assert len(session.history) == 2
    assert session.history[1].role == "assistant"
    assert session.history[1].content == "echo: hello"


async def test_gateway_stream_saves_session() -> None:
    """流结束后 session 应被持久化。"""
    store = InMemorySessionStore()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=EchoAgentRunner(),
        delivery=LocalDelivery(),
    )
    async for _ in gateway.handle_stream(_msg("hello")):
        pass
    session = await store.get("local:app:user")
    assert session is not None


async def test_gateway_stream_sends_complete_reply_to_delivery() -> None:
    """流结束后 Delivery 应收到完整回复。"""
    delivery = LocalDelivery()
    gateway = RuntimeGateway(
        session_store=InMemorySessionStore(),
        agent_runner=EchoAgentRunner(),
        delivery=delivery,
    )
    async for _ in gateway.handle_stream(_msg("hello")):
        pass
    assert len(delivery.sent) == 1
    assert delivery.sent[0][1].text == "echo: hello"


async def test_gateway_stream_reuses_existing_session() -> None:
    """同一 session_key 的多次流式消息应复用同一个 Session。"""
    store = InMemorySessionStore()
    existing = create_session(_msg())
    await store.save(existing)
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=EchoAgentRunner(),
        delivery=LocalDelivery(),
    )
    async for _ in gateway.handle_stream(_msg("first")):
        pass
    async for _ in gateway.handle_stream(_msg("second")):
        pass
    session = await store.get("local:app:user")
    assert len(session.history) == 4


# --- handle_stream thinking 测试 ---


class _ThinkingRunner:
    """模拟产出 thinking + content 的 Agent Runner。"""

    async def run(self, session, message):
        from claw.types import ChatMessage as CM, AgentReply as AR
        session.history.append(CM(role="user", content=message.text))
        session.history.append(CM(role="assistant", content="answer"))
        return AR(text="answer")

    async def run_stream(self, session, message):
        from claw.types import ChatMessage as CM
        session.history.append(CM(role="user", content=message.text))
        yield StreamChunk(type="thinking", text="let me think...")
        yield StreamChunk(type="content", text="answer")


async def test_gateway_stream_accumulates_thinking_separately() -> None:
    """thinking chunk 不应写入 history，但应出现在 Delivery 的 metadata 中。"""
    delivery = LocalDelivery()
    gateway = RuntimeGateway(
        session_store=InMemorySessionStore(),
        agent_runner=_ThinkingRunner(),
        delivery=delivery,
    )
    chunks: list[StreamChunk] = []
    async for chunk in gateway.handle_stream(_msg("hello")):
        chunks.append(chunk)

    # history 只有 user + assistant(content)，不含 thinking
    msg, reply = delivery.sent[0]
    assert len(chunks) == 2
    assert reply.metadata.get("reasoning") == "let me think..."


async def test_gateway_stream_content_only_no_thinking_in_metadata() -> None:
    """纯 content（无 thinking）时 Delivery 的 metadata 不含 reasoning。"""
    delivery = LocalDelivery()
    gateway = RuntimeGateway(
        session_store=InMemorySessionStore(),
        agent_runner=EchoAgentRunner(),
        delivery=delivery,
    )
    async for _ in gateway.handle_stream(_msg("hello")):
        pass
    _, reply = delivery.sent[0]
    assert "reasoning" not in reply.metadata
