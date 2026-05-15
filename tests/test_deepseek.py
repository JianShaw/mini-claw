"""DeepSeek Agent Runner 测试：验证流式模式、工具调用、思考模式等。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from claw.deepseek import DeepSeekAgentRunner
from claw.session import create_session
from claw.tools import Tool, ToolsRegistry
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
    delta.tool_calls = None
    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = "stop"
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


def _mock_tool_call_chunk(idx: int, tc_id: str = "", name: str = "", arguments: str = "") -> MagicMock:
    """构造一个模拟的 stream chunk 包含 tool_calls delta。"""
    tc_delta = MagicMock()
    tc_delta.index = idx
    tc_delta.id = tc_id
    tc_delta.function = MagicMock()
    tc_delta.function.name = name
    tc_delta.function.arguments = arguments
    delta = MagicMock()
    delta.content = None
    delta.reasoning_content = None
    delta.tool_calls = [tc_delta]
    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = "tool_calls" if tc_id else None
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


def _mock_response(tool_calls=None, content=None, reasoning_content=None):
    """构造一个模拟的 chat completion response。"""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.reasoning_content = reasoning_content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _mock_tool_call(tc_id: str, name: str, arguments: str):
    """构造一个模拟的 tool_call 对象。"""
    tc = MagicMock()
    tc.id = tc_id
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


async def _echo_handler(args: dict) -> str:
    return args.get("text", "")


# --- 原有测试 ---


async def test_deepseek_run_sends_deepseek_thinking_object() -> None:
    runner = DeepSeekAgentRunner(api_key="test", thinking=True)
    response = _mock_response(content="answer", reasoning_content="reasoning")
    runner.client.chat.completions.create = AsyncMock(return_value=response)

    await runner.run(create_session(_msg()), _msg("hello"))

    kwargs = runner.client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"]["thinking"] == {
        "type": "enabled",
        "reasoning_effort": "high",
    }


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
    assert result[0].text == "answer"


async def test_deepseek_stream_yields_thinking_then_content() -> None:
    runner = DeepSeekAgentRunner(api_key="test", thinking=True)

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
    assert result[2].type == "content"
    assert result[2].text == "the "


async def test_deepseek_stream_without_thinking_yields_content_only() -> None:
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


# --- _build_messages 测试 ---


async def test_build_messages_includes_summary_as_system() -> None:
    runner = DeepSeekAgentRunner(api_key="test", thinking=False)

    session = create_session(_msg())
    session.summary = "之前讨论了排序算法"
    session.history.append(ChatMessage(role="user", content="继续"))
    session.history.append(ChatMessage(role="assistant", content="好的"))

    messages = runner._build_messages(session)
    assert len(messages) == 3
    assert messages[0]["role"] == "system"
    assert "排序算法" in messages[0]["content"]


async def test_build_messages_without_summary_no_system() -> None:
    runner = DeepSeekAgentRunner(api_key="test", thinking=False)

    session = create_session(_msg())
    session.history.append(ChatMessage(role="user", content="hello"))

    messages = runner._build_messages(session)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


async def test_build_messages_includes_memory_context_as_system() -> None:
    runner = DeepSeekAgentRunner(api_key="test", thinking=False)

    session = create_session(_msg())
    session.metadata["memory_context"] = "[Memory Context]\n- remembered"
    session.history.append(ChatMessage(role="user", content="hello"))

    messages = runner._build_messages(session)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "remembered" in messages[0]["content"]
    assert messages[1]["role"] == "user"


# --- 工具调用测试 ---


async def test_run_without_tools_no_tools_param() -> None:
    """没有 registry 时 API 调用不应包含 tools 参数。"""
    runner = DeepSeekAgentRunner(api_key="test", thinking=False)
    response = _mock_response(content="hello")
    runner.client.chat.completions.create = AsyncMock(return_value=response)

    await runner.run(create_session(_msg()), _msg("hi"))

    kwargs = runner.client.chat.completions.create.call_args.kwargs
    assert "tools" not in kwargs


async def test_run_with_empty_registry_no_tools_param() -> None:
    """空 registry 时 API 调用不应包含 tools 参数。"""
    registry = ToolsRegistry()
    runner = DeepSeekAgentRunner(api_key="test", thinking=False, tools_registry=registry)
    response = _mock_response(content="hello")
    runner.client.chat.completions.create = AsyncMock(return_value=response)

    await runner.run(create_session(_msg()), _msg("hi"))

    kwargs = runner.client.chat.completions.create.call_args.kwargs
    assert "tools" not in kwargs


async def test_run_with_tools_passes_tools_to_api() -> None:
    """有注册工具时 API 调用应包含 tools 参数。"""
    registry = ToolsRegistry()
    registry.register(Tool(
        name="calculator", description="math", handler=_echo_handler,
        parameters={"type": "object", "properties": {"expression": {"type": "string"}}},
    ))
    runner = DeepSeekAgentRunner(api_key="test", thinking=False, tools_registry=registry)
    response = _mock_response(content="done")
    runner.client.chat.completions.create = AsyncMock(return_value=response)

    await runner.run(create_session(_msg()), _msg("calc"))

    kwargs = runner.client.chat.completions.create.call_args.kwargs
    assert "tools" in kwargs
    assert kwargs["tools"][0]["function"]["name"] == "calculator"


async def test_run_executes_tool_and_returns_result() -> None:
    """LLM 返回 tool_calls 时应执行工具并返回最终结果。"""
    registry = ToolsRegistry()
    registry.register(Tool(name="echo", description="echo", handler=_echo_handler))
    runner = DeepSeekAgentRunner(api_key="test", thinking=False, tools_registry=registry)

    # 第一次调用返回 tool_calls，第二次返回文本
    tc = _mock_tool_call("call_1", "echo", '{"text": "hello"}')
    first_response = _mock_response(tool_calls=[tc])
    second_response = _mock_response(content="The echo result is hello")
    runner.client.chat.completions.create = AsyncMock(
        side_effect=[first_response, second_response]
    )

    session = create_session(_msg())
    reply = await runner.run(session, _msg("echo hello"))

    assert reply.text == "The echo result is hello"
    # session history 应包含 user, assistant(tool_calls), tool(result), assistant(final)
    assert len(session.history) == 4
    assert session.history[1].role == "assistant"
    assert session.history[1].tool_calls is not None
    assert session.history[2].role == "tool"
    assert session.history[2].tool_call_id == "call_1"
    assert session.history[2].tool_name == "echo"
    assert session.history[2].content == "hello"

    # 第二次 API 调用的 messages 中应包含 type 字段
    second_call_kwargs = runner.client.chat.completions.create.call_args_list[1].kwargs
    second_messages = second_call_kwargs["messages"]
    assistant_msg = [m for m in second_messages if m.get("tool_calls")][0]
    assert assistant_msg["tool_calls"][0]["type"] == "function"


async def test_run_multiple_tool_calls_in_one_response() -> None:
    """一次 LLM 响应中包含多个工具调用时应全部执行。"""
    registry = ToolsRegistry()
    registry.register(Tool(name="echo", description="echo", handler=_echo_handler))
    runner = DeepSeekAgentRunner(api_key="test", thinking=False, tools_registry=registry)

    tc1 = _mock_tool_call("call_1", "echo", '{"text": "a"}')
    tc2 = _mock_tool_call("call_2", "echo", '{"text": "b"}')
    first_response = _mock_response(tool_calls=[tc1, tc2])
    second_response = _mock_response(content="done")
    runner.client.chat.completions.create = AsyncMock(
        side_effect=[first_response, second_response]
    )

    session = create_session(_msg())
    reply = await runner.run(session, _msg("echo both"))

    assert reply.text == "done"
    # user + assistant(tool_calls) + tool1 + tool2 + assistant(final) = 5
    assert len(session.history) == 5
    assert session.history[2].tool_call_id == "call_1"
    assert session.history[3].tool_call_id == "call_2"


async def test_run_tool_error_graceful() -> None:
    """工具 handler 抛异常时应返回错误信息给 LLM。"""
    async def _fail_handler(args: dict) -> str:
        raise ValueError("something went wrong")

    registry = ToolsRegistry()
    registry.register(Tool(name="fail", description="fail", handler=_fail_handler))
    runner = DeepSeekAgentRunner(api_key="test", thinking=False, tools_registry=registry)

    tc = _mock_tool_call("call_1", "fail", '{}')
    first_response = _mock_response(tool_calls=[tc])
    second_response = _mock_response(content="Tool failed")
    runner.client.chat.completions.create = AsyncMock(
        side_effect=[first_response, second_response]
    )

    session = create_session(_msg())
    reply = await runner.run(session, _msg("fail"))

    assert reply.text == "Tool failed"
    # 工具结果消息应包含错误信息
    tool_msg = session.history[2]
    assert tool_msg.role == "tool"
    assert "Tool error" in tool_msg.content
    assert "ValueError" in tool_msg.content


async def test_run_tool_iteration_limit() -> None:
    """超过最大迭代次数时应返回提示。"""
    registry = ToolsRegistry()
    registry.register(Tool(name="echo", description="echo", handler=_echo_handler))
    runner = DeepSeekAgentRunner(
        api_key="test", thinking=False,
        tools_registry=registry, max_tool_iterations=2,
    )

    # 每次都返回 tool_calls，模拟无限循环
    tc = _mock_tool_call("call_1", "echo", '{"text": "loop"}')
    loop_response = _mock_response(tool_calls=[tc])
    runner.client.chat.completions.create = AsyncMock(return_value=loop_response)

    session = create_session(_msg())
    reply = await runner.run(session, _msg("loop"))

    assert reply.text == "[tool call limit reached]"
    assert reply.metadata.get("tool_iterations_exhausted") is True


async def test_run_invalid_json_arguments() -> None:
    """工具调用参数为非法 JSON 时应捕获异常。"""
    registry = ToolsRegistry()
    registry.register(Tool(name="echo", description="echo", handler=_echo_handler))
    runner = DeepSeekAgentRunner(api_key="test", thinking=False, tools_registry=registry)

    tc = _mock_tool_call("call_1", "echo", "not json")
    first_response = _mock_response(tool_calls=[tc])
    second_response = _mock_response(content="handled")
    runner.client.chat.completions.create = AsyncMock(
        side_effect=[first_response, second_response]
    )

    session = create_session(_msg())
    reply = await runner.run(session, _msg("bad args"))

    assert reply.text == "handled"
    tool_msg = session.history[2]
    assert "Tool error" in tool_msg.content


async def test_build_messages_handles_tool_history() -> None:
    """_build_messages 应正确序列化工具调用和工具结果消息。"""
    runner = DeepSeekAgentRunner(api_key="test", thinking=False)

    session = create_session(_msg())
    session.history.append(ChatMessage(role="user", content="calc"))
    session.history.append(ChatMessage(
        role="assistant", content="",
        tool_calls=[{"id": "c1", "function": {"name": "calc", "arguments": "{}"}}],
    ))
    session.history.append(ChatMessage(
        role="tool", content="42",
        tool_call_id="c1", tool_name="calc",
    ))

    messages = runner._build_messages(session)
    assert len(messages) == 3
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] is None
    assert messages[1]["tool_calls"] is not None
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "c1"
    assert messages[2]["content"] == "42"


async def test_stream_with_tool_calls() -> None:
    """流式模式下 LLM 返回工具调用时应执行工具并继续流式输出。"""
    registry = ToolsRegistry()
    registry.register(Tool(name="echo", description="echo", handler=_echo_handler))
    runner = DeepSeekAgentRunner(api_key="test", thinking=False, tools_registry=registry)

    # 第一次流：tool_call delta
    stream1_chunks = [
        _mock_tool_call_chunk(0, tc_id="call_1", name="echo", arguments='{"text":'),
        _mock_tool_call_chunk(0, arguments='"hi"}'),
    ]
    # 设置最后一个 chunk 的 finish_reason
    stream1_chunks[-1].choices[0].finish_reason = "tool_calls"

    # 第二次流：正常 content
    stream2_chunks = [_mock_chunk(content="result")]

    runner.client.chat.completions.create = AsyncMock(
        side_effect=[_FakeStream(stream1_chunks), _FakeStream(stream2_chunks)]
    )

    session = create_session(_msg())
    result: list[StreamChunk] = []
    async for chunk in runner.run_stream(session, _msg("echo hi")):
        result.append(chunk)

    # 应包含 tool_call 通知、tool_result 通知和最终的 content
    types = [c.type for c in result]
    assert "tool_call" in types
    assert "tool_result" in types
    assert "content" in types

    # session history 应包含工具调用消息
    assert any(m.role == "tool" for m in session.history)
