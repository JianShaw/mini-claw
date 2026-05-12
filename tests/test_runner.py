"""Agent 运行器测试：验证 EchoAgentRunner 的回声回复和对话历史维护。"""

from __future__ import annotations

from claw.runner import EchoAgentRunner
from claw.session import create_session
from claw.types import ChatMessage, InboundMessage, StreamChunk


def _msg(text: str = "hello") -> InboundMessage:
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


async def test_echo_agent_runner_returns_echo_reply() -> None:
    runner = EchoAgentRunner()
    session = create_session(_msg())
    reply = await runner.run(session, _msg("hello"))
    assert reply.text == "echo: hello"


async def test_echo_agent_runner_appends_user_and_assistant_history() -> None:
    runner = EchoAgentRunner()
    session = create_session(_msg())
    await runner.run(session, _msg("hello"))
    assert len(session.history) == 2
    assert session.history[0].role == "user"
    assert session.history[0].content == "hello"
    assert session.history[1].role == "assistant"
    assert session.history[1].content == "echo: hello"


async def test_echo_agent_runner_preserves_existing_history() -> None:
    runner = EchoAgentRunner()
    session = create_session(_msg())
    session.history.append(ChatMessage(role="user", content="prev"))
    await runner.run(session, _msg("new"))
    assert len(session.history) == 3
    assert session.history[0].content == "prev"


# --- run_stream 测试 ---


async def test_echo_agent_runner_stream_yields_text() -> None:
    """run_stream 应 yield 正确的 StreamChunk。"""
    runner = EchoAgentRunner()
    session = create_session(_msg())
    chunks: list[StreamChunk] = []
    async for chunk in runner.run_stream(session, _msg("hello")):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].type == "content"
    assert chunks[0].text == "echo: hello"


async def test_echo_agent_runner_stream_appends_user_history() -> None:
    """run_stream 应将 user 消息追加到 history。"""
    runner = EchoAgentRunner()
    session = create_session(_msg())
    async for _ in runner.run_stream(session, _msg("hello")):
        pass
    assert len(session.history) == 1
    assert session.history[0].role == "user"
    assert session.history[0].content == "hello"


async def test_echo_agent_runner_stream_does_not_append_assistant() -> None:
    """run_stream 不应追加 assistant 到 history（由 Gateway 负责）。"""
    runner = EchoAgentRunner()
    session = create_session(_msg())
    async for _ in runner.run_stream(session, _msg("hello")):
        pass
    assert all(m.role != "assistant" for m in session.history)


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


async def test_echo_agent_runner_returns_echo_reply() -> None:
    """Runner 应返回 "echo: {用户输入}"。"""
    runner = EchoAgentRunner()
    session = create_session(_msg())
    reply = await runner.run(session, _msg("hello"))
    assert reply.text == "echo: hello"


async def test_echo_agent_runner_appends_user_and_assistant_history() -> None:
    """每次 run 应往 history 追加 user 和 assistant 两条记录。"""
    runner = EchoAgentRunner()
    session = create_session(_msg())
    await runner.run(session, _msg("hello"))
    assert len(session.history) == 2
    assert session.history[0].role == "user"
    assert session.history[0].content == "hello"
    assert session.history[1].role == "assistant"
    assert session.history[1].content == "echo: hello"


async def test_echo_agent_runner_preserves_existing_history() -> None:
    """新 run 不应覆盖已有的历史记录。"""
    runner = EchoAgentRunner()
    session = create_session(_msg())
    session.history.append(ChatMessage(role="user", content="prev"))
    await runner.run(session, _msg("new"))
    assert len(session.history) == 3
    assert session.history[0].content == "prev"
