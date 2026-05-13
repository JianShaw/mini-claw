"""上下文压缩器测试：验证 should_compress 判断、compress 分割逻辑和边界情况。"""

from __future__ import annotations

from unittest.mock import AsyncMock

from claw.compressor import ContextCompressor
from claw.tokens import estimate_session_tokens
from claw.types import ChatMessage, Session


def _session(
    history: list[ChatMessage] | None = None,
    summary: str | None = None,
    history_offset: int = 0,
) -> Session:
    """构造测试用 Session。"""
    return Session(
        session_id="test",
        session_key="local:app:user",
        channel="local",
        account_id="app",
        peer_id="user",
        sender_id="user",
        agent_id="default",
        history=history or [],
        summary=summary,
        history_offset=history_offset,
    )


def _make_history(rounds: int, content_len: int = 50) -> list[ChatMessage]:
    """生成指定轮数的对话历史，每条消息内容足够长以触发阈值。"""
    messages: list[ChatMessage] = []
    for i in range(rounds):
        messages.append(ChatMessage(role="user", content=f"Question {i}: " + "x" * content_len))
        messages.append(ChatMessage(role="assistant", content=f"Answer {i}: " + "y" * content_len))
    return messages


def _compressor(max_tokens: int = 100, keep_rounds: int = 2, enabled: bool = True) -> ContextCompressor:
    """创建测试用 compressor，使用 mock client。"""
    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock()]
    mock_response.choices[0].message.content = "Generated summary"
    mock_client.chat.completions.create.return_value = mock_response

    return ContextCompressor(
        client=mock_client,
        model="test-model",
        max_tokens=max_tokens,
        keep_rounds=keep_rounds,
        enabled=enabled,
    )


# --- should_compress ---


def test_should_compress_below_threshold() -> None:
    """token 数低于阈值时不压缩。"""
    c = _compressor(max_tokens=10000)
    s = _session(history=_make_history(3))
    assert not c.should_compress(s)


def test_should_compress_above_threshold() -> None:
    """token 数超过阈值时需要压缩。"""
    c = _compressor(max_tokens=50, keep_rounds=1)
    # 5 轮 = 10 条消息，内容很长，肯定超阈值
    s = _session(history=_make_history(5, content_len=100))
    assert c.should_compress(s)


def test_should_compress_disabled() -> None:
    """enabled=False 时始终不压缩。"""
    c = _compressor(max_tokens=1, enabled=False)
    s = _session(history=_make_history(10, content_len=100))
    assert not c.should_compress(s)


def test_should_compress_too_few_messages() -> None:
    """消息不足 keep_rounds*2+2 时不压缩，即使 token 超阈值。"""
    c = _compressor(max_tokens=1, keep_rounds=2)
    # keep_rounds=2 → 需要 2*2+2=6 条消息
    s = _session(history=_make_history(2, content_len=100))  # 4 条消息
    assert not c.should_compress(s)


def test_should_compress_includes_incoming_text() -> None:
    """incoming_text 计入总量，追加后可能超阈值。"""
    c = _compressor(max_tokens=200, keep_rounds=1)
    s = _session(history=_make_history(2, content_len=50))
    # 不带 incoming_text：可能不超
    base_tokens = estimate_session_tokens(s)
    # 带 incoming_text：加上一条很长的消息
    if base_tokens <= 200:
        assert c.should_compress(s, incoming_text="x" * 1000)


# --- compress 核心逻辑 ---


async def test_compress_splits_at_keep_rounds() -> None:
    """compress 保留最近 keep_rounds 轮，压缩更早的消息。"""
    c = _compressor(keep_rounds=2)
    s = _session(history=_make_history(4))  # 8 条消息，保留 4 条（2 轮）

    result = await c.compress(s)

    assert result is not None
    assert len(s.history) == 4  # 保留 2 轮 = 4 条
    # 保留的是最后 2 轮：Q2,A2,Q3,A3
    assert "Question 2" in s.history[0].content
    assert "Answer 2" in s.history[1].content
    assert "Question 3" in s.history[2].content
    assert "Answer 3" in s.history[3].content


async def test_compress_updates_summary() -> None:
    """compress 成功时设置 summary。"""
    c = _compressor()
    s = _session(history=_make_history(3))

    await c.compress(s)
    assert s.summary == "Generated summary"


async def test_compress_updates_history_offset() -> None:
    """compress 成功时 history_offset 累加 split_point。"""
    c = _compressor(keep_rounds=1)
    s = _session(history=_make_history(3), history_offset=0)

    await c.compress(s)
    # keep_rounds=1，保留最后 2 条，split_point = 4
    assert s.history_offset == 4


async def test_compress_with_existing_summary() -> None:
    """已有 summary 时传给 LLM 让其合并。"""
    c = _compressor()
    s = _session(history=_make_history(3), summary="Old summary")

    await c.compress(s)

    # 验证 LLM 收到的 prompt 包含已有摘要
    call_args = c._client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    user_msg = messages[1]["content"]
    assert "已有摘要" in user_msg
    assert "Old summary" in user_msg


async def test_compress_returns_none_when_nothing_to_compress() -> None:
    """历史不足 keep_rounds 时 compress 返回 None。"""
    c = _compressor(keep_rounds=5)
    s = _session(history=_make_history(2))  # 只有 2 轮，不够 5 轮

    result = await c.compress(s)
    assert result is None


async def test_compress_returns_none_on_llm_failure() -> None:
    """LLM 调用失败时 session 不变。"""
    c = _compressor()
    c._client.chat.completions.create.side_effect = RuntimeError("API error")

    original_history = _make_history(4)
    s = _session(history=original_history[:], summary=None, history_offset=0)

    result = await c.compress(s)
    assert result is None
    assert len(s.history) == len(original_history)
    assert s.summary is None
    assert s.history_offset == 0


async def test_compress_force_bypasses_threshold() -> None:
    """force=True 时即使 token 不超阈值也压缩。"""
    c = _compressor(max_tokens=100000, enabled=False)  # 不可能触发自动压缩
    s = _session(history=_make_history(4))

    # should_compress 返回 False
    assert not c.should_compress(s)
    # 但 force=True 仍可压缩
    result = await c.compress(s, force=True)
    assert result is not None
    assert len(s.history) < 8


# --- _find_split_point 边界 ---


def test_find_split_point_exact_rounds() -> None:
    """history 恰好等于 keep_rounds 时 split_point 为 0。"""
    c = _compressor(keep_rounds=3)
    s = _session(history=_make_history(3))
    assert c._find_split_point(s) == 0


def test_find_split_point_all_in_keep() -> None:
    """短 history 全部在 keep 范围内，返回 0。"""
    c = _compressor(keep_rounds=10)
    s = _session(history=_make_history(2))
    assert c._find_split_point(s) == 0


def test_find_split_point_trailing_user() -> None:
    """末尾有不成对的 user 消息时仍正确定位。"""
    c = _compressor(keep_rounds=2)
    history = _make_history(3)  # 6 条
    history.append(ChatMessage(role="user", content="orphan user"))  # 7 条
    s = _session(history=history)

    split = c._find_split_point(s)
    assert split > 0
    # recent 部分应从 user 开始
    assert s.history[split].role == "user"


def test_find_split_point_only_user_messages() -> None:
    """只有 user 消息时 split_point 为 0（无法形成有效摘要）。"""
    c = _compressor(keep_rounds=2)
    history = [ChatMessage(role="user", content=f"q{i}") for i in range(10)]
    s = _session(history=history)
    assert c._find_split_point(s) == 0


def test_find_split_point_recent_starts_with_user() -> None:
    """split 后的 recent 部分必须从 user 消息开始。"""
    c = _compressor(keep_rounds=2)
    history = [
        ChatMessage(role="user", content="q0"),
        ChatMessage(role="assistant", content="a0"),
        ChatMessage(role="user", content="q1"),
        ChatMessage(role="assistant", content="a1"),
        ChatMessage(role="user", content="q2"),
        ChatMessage(role="assistant", content="a2"),
        ChatMessage(role="user", content="q3"),
        ChatMessage(role="assistant", content="a3"),
    ]
    s = _session(history=history)
    split = c._find_split_point(s)
    assert split > 0
    assert s.history[split].role == "user"
