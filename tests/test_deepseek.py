"""DeepSeek Agent Runner 测试：验证流式模式下 thinking 和 content 的处理。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from claw.deepseek import DeepSeekAgentRunner
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


class _FakeStream:
    """模拟 OpenAI async stream 的异步迭代器。"""

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration


def _mock_chunk(reasoning_content: str | None = None, content: str | None = None) -> MagicMock:
    """构造一个模拟的 stream chunk。"""
    delta = MagicMock()
    delta.content = content
    delta.reasoning_content = reasoning_content
    choice = MagicMock()
    choice.delta = delta
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


async def test_deepseek_run_sends_deepseek_thinking_object() -> None:
    runner = DeepSeekAgentRunner(api_key="test", thinking=True)
    msg = MagicMock()
    msg.content = "answer"
    msg.reasoning_content = "reasoning"
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    runner.client.chat.completions.create = AsyncMock(return_value=response)

    await runner.run(create_session(_msg()), _msg("hello"))

    kwargs = runner.client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"]["thinking"] == {
        "type": "enabled",
        "reasoning_effort": "high",
    }
    assert "thinking" not in kwargs
    assert "reasoning_effort" not in kwargs


async def test_deepseek_run_stream_sends_disabled_thinking_object() -> None:
    runner = DeepSeekAgentRunner(api_key="test", thinking=False)
    runner.client.chat.completions.create = AsyncMock(
        return_value=_FakeStream([_mock_chunk(content="answer")])
    )

    result: list[StreamChunk] = []
    async for chunk in runner.run_stream(create_session(_msg()), _msg("hello")):
        result.append(chunk)

    kwargs = runner.client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"]["thinking"] == {
        "type": "disabled",
        "reasoning_effort": "high",
    }
    assert "thinking" not in kwargs
    assert "reasoning_effort" not in kwargs
    assert result[0].text == "answer"


async def test_deepseek_stream_yields_thinking_then_content() -> None:
    """thinking 模式下应先 yield thinking chunk，再 yield content chunk。"""
    runner = DeepSeekAgentRunner(api_key="test", thinking=True)

    # 模拟流式响应：先思考，再正文
    chunks_data = [
        _mock_chunk(reasoning_content="let me"),
        _mock_chunk(reasoning_content=" think"),
        _mock_chunk(content="the "),
        _mock_chunk(content="answer"),
    ]

    runner.client.chat.completions.create = AsyncMock(return_value=_FakeStream(chunks_data))

    session = create_session(_msg())
    result: list[StreamChunk] = []
    async for chunk in runner.run_stream(session, _msg("hello")):
        result.append(chunk)

    assert len(result) == 4
    assert result[0].type == "thinking"
    assert result[0].text == "let me"
    assert result[1].type == "thinking"
    assert result[1].text == " think"
    assert result[2].type == "content"
    assert result[2].text == "the "
    assert result[3].type == "content"
    assert result[3].text == "answer"


async def test_deepseek_stream_without_thinking_yields_content_only() -> None:
    """非 thinking 模式下只 yield content chunk。"""
    runner = DeepSeekAgentRunner(api_key="test", thinking=False)

    chunks_data = [
        _mock_chunk(content="hello "),
        _mock_chunk(content="world"),
    ]

    runner.client.chat.completions.create = AsyncMock(return_value=_FakeStream(chunks_data))

    session = create_session(_msg())
    result: list[StreamChunk] = []
    async for chunk in runner.run_stream(session, _msg("hi")):
        result.append(chunk)

    assert all(c.type == "content" for c in result)
    texts = "".join(c.text for c in result)
    assert texts == "hello world"


async def test_deepseek_stream_empty_chunks_skipped() -> None:
    """空 reasoning 和空 content 不应 yield。"""
    runner = DeepSeekAgentRunner(api_key="test", thinking=True)

    chunks_data = [
        _mock_chunk(reasoning_content=None, content=None),
        _mock_chunk(reasoning_content="", content=""),
        _mock_chunk(content="real"),
    ]

    runner.client.chat.completions.create = AsyncMock(return_value=_FakeStream(chunks_data))

    session = create_session(_msg())
    result: list[StreamChunk] = []
    async for chunk in runner.run_stream(session, _msg("hi")):
        result.append(chunk)

    assert len(result) == 1
    assert result[0].text == "real"


# --- _build_messages with summary 测试 ---


async def test_build_messages_includes_summary_as_system() -> None:
    """session 有 summary 时 _build_messages 应在开头插入 system message。"""
    runner = DeepSeekAgentRunner(api_key="test", thinking=False)

    session = create_session(_msg())
    session.summary = "之前讨论了排序算法"
    session.history.append(ChatMessage(role="user", content="继续"))
    session.history.append(ChatMessage(role="assistant", content="好的"))

    messages = runner._build_messages(session)
    assert len(messages) == 3
    assert messages[0]["role"] == "system"
    assert "排序算法" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"


async def test_build_messages_without_summary_no_system() -> None:
    """session 没有 summary 时 _build_messages 不插入 system message。"""
    runner = DeepSeekAgentRunner(api_key="test", thinking=False)

    session = create_session(_msg())
    session.history.append(ChatMessage(role="user", content="hello"))

    messages = runner._build_messages(session)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
