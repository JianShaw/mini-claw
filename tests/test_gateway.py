"""Gateway 测试：验证会话创建/复用、Agent 调度、回复投递、会话管理方法。"""

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


def _msg_other_peer(text: str = "hello") -> InboundMessage:
    """构造不同 peer 的 InboundMessage（peer_id 不同）。"""
    return InboundMessage(
        channel="local",
        account_id="app",
        peer_id="other-user",
        sender_id="other-user",
        message_id="2",
        text=text,
        timestamp=0,
        message_type="text",
        raw=None,
    )


# --- handle_inbound_message 测试 ---


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
    session = await store.get_active("local:app:user")
    assert session is not None
    assert len(session.history) == 2


async def test_gateway_reuses_existing_session() -> None:
    """同一 peer 的多次消息应复用同一个 Session，history 累积。"""
    store = InMemorySessionStore()
    delivery = LocalDelivery()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=EchoAgentRunner(),
        delivery=delivery,
    )
    await gateway.handle_inbound_message(_msg("first"))
    await gateway.handle_inbound_message(_msg("second"))
    session = await store.get_active("local:app:user")
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
    session = await store.get_active("local:app:user")
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
    session = await store.get_active("local:app:user")
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
    """同一 peer 的多次流式消息应复用同一个 Session。"""
    store = InMemorySessionStore()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=EchoAgentRunner(),
        delivery=LocalDelivery(),
    )
    async for _ in gateway.handle_stream(_msg("first")):
        pass
    async for _ in gateway.handle_stream(_msg("second")):
        pass
    session = await store.get_active("local:app:user")
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


# --- 会话管理方法测试 ---


async def test_gateway_create_new_session() -> None:
    """create_new_session 应创建新 session 并激活。"""
    store = InMemorySessionStore()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=EchoAgentRunner(),
        delivery=LocalDelivery(),
    )
    # 首条消息自动创建 session
    await gateway.handle_inbound_message(_msg("first"))
    first_session = await store.get_active("local:app:user")

    # 手动创建新 session
    new_session = await gateway.create_new_session(_msg())
    assert new_session.session_id != first_session.session_id
    # 活跃 session 应切换到新建的
    active = await store.get_active("local:app:user")
    assert active.session_id == new_session.session_id


async def test_gateway_list_sessions() -> None:
    """list_sessions 应列出 peer 下所有 session。"""
    store = InMemorySessionStore()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=EchoAgentRunner(),
        delivery=LocalDelivery(),
    )
    await gateway.handle_inbound_message(_msg("first"))
    await gateway.create_new_session(_msg())

    sessions = await gateway.list_sessions(_msg())
    assert len(sessions) == 2


async def test_gateway_select_session() -> None:
    """select_session 应切换活跃 session。"""
    store = InMemorySessionStore()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=EchoAgentRunner(),
        delivery=LocalDelivery(),
    )
    await gateway.handle_inbound_message(_msg("first"))
    s1 = await store.get_active("local:app:user")

    new_session = await gateway.create_new_session(_msg())

    # 切回 s1
    result = await gateway.select_session(_msg(), s1.session_id)
    assert result is not None
    assert result.session_id == s1.session_id
    active = await store.get_active("local:app:user")
    assert active.session_id == s1.session_id


async def test_gateway_select_session_not_found() -> None:
    """select_session 找不到时返回 None。"""
    store = InMemorySessionStore()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=EchoAgentRunner(),
        delivery=LocalDelivery(),
    )
    result = await gateway.select_session(_msg(), "nonexistent")
    assert result is None


async def test_gateway_select_session_rejects_other_peer() -> None:
    """select_session 不允许切换到其他 peer 的 session。"""
    store = InMemorySessionStore()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=EchoAgentRunner(),
        delivery=LocalDelivery(),
    )
    # peer A 创建 session
    await gateway.handle_inbound_message(_msg("hi from A"))
    s_a = await store.get_active("local:app:user")

    # peer B 创建 session
    await gateway.handle_inbound_message(_msg_other_peer("hi from B"))
    s_b = await store.get_active("local:app:other-user")

    # peer A 尝试 select peer B 的 session → 应返回 None
    result = await gateway.select_session(_msg(), s_b.session_id)
    assert result is None
    # peer A 的 active 没变
    active = await store.get_active("local:app:user")
    assert active.session_id == s_a.session_id


async def test_gateway_delete_session() -> None:
    """delete_session 应删除指定 session。"""
    store = InMemorySessionStore()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=EchoAgentRunner(),
        delivery=LocalDelivery(),
    )
    await gateway.handle_inbound_message(_msg("first"))
    s1 = await store.get_active("local:app:user")
    new_session = await gateway.create_new_session(_msg())

    await gateway.delete_session(_msg(), s1.session_id)
    sessions = await gateway.list_sessions(_msg())
    assert len(sessions) == 1
    assert sessions[0].session_id == new_session.session_id


# --- compact_session 测试 ---


class _SummaryRunner:
    """模拟摘要生成的 Agent Runner：将 prompt 中的关键内容返回作为摘要。"""

    async def run(self, session, message):
        from claw.types import ChatMessage as CM, AgentReply as AR
        session.history.append(CM(role="user", content=message.text))
        summary = f"summary: {message.text[:20]}"
        session.history.append(CM(role="assistant", content=summary))
        return AR(text=summary)

    async def run_stream(self, session, message):
        yield StreamChunk(type="content", text="summary")


async def test_gateway_compact_session_generates_summary() -> None:
    """compact_session 应生成摘要、设置到 session.summary、清空 history。"""
    store = InMemorySessionStore()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=_SummaryRunner(),
        delivery=LocalDelivery(),
    )
    # 先发几条消息积累 history
    await gateway.handle_inbound_message(_msg("讨论了排序算法"))
    session = await store.get_active("local:app:user")
    assert len(session.history) > 0

    # compact
    summary = await gateway.compact_session(_msg())
    assert summary is not None

    # 验证 session 状态
    session = await store.get_active("local:app:user")
    assert session.summary == summary
    assert len(session.history) == 0


async def test_gateway_compact_empty_session_returns_none() -> None:
    """compact 空 session 应返回 None。"""
    store = InMemorySessionStore()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=_SummaryRunner(),
        delivery=LocalDelivery(),
    )
    result = await gateway.compact_session(_msg())
    assert result is None


async def test_gateway_compact_no_session_returns_none() -> None:
    """没有活跃 session 时 compact 返回 None。"""
    store = InMemorySessionStore()
    gateway = RuntimeGateway(
        session_store=store,
        agent_runner=_SummaryRunner(),
        delivery=LocalDelivery(),
    )
    result = await gateway.compact_session(_msg())
    assert result is None
