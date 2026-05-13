"""Token 估算测试：验证 heuristic 对不同语言文本的估算准确性。"""

from __future__ import annotations

from claw.tokens import estimate_session_tokens, estimate_tokens
from claw.types import ChatMessage, Session


def _session(history: list[ChatMessage] | None = None, summary: str | None = None) -> Session:
    """构造测试用 Session。"""
    return Session(
        session_id="test",
        session_key="test",
        channel="local",
        account_id="app",
        peer_id="user",
        sender_id="user",
        agent_id="default",
        history=history or [],
        summary=summary,
    )


# --- estimate_tokens ---


def test_estimate_tokens_empty_string() -> None:
    """空字符串返回 0。"""
    assert estimate_tokens("") == 0


def test_estimate_tokens_pure_latin() -> None:
    """纯拉丁文 ~4 字符/token。"""
    text = "Hello world, this is a test."
    result = estimate_tokens(text)
    expected = max(1, len(text) // 4)
    assert result == expected


def test_estimate_tokens_pure_cjk() -> None:
    """纯 CJK 文本 ~1.5 字符/token。"""
    text = "你好世界测试"
    result = estimate_tokens(text)
    expected = max(1, int(len(text) / 1.5))
    assert result == expected


def test_estimate_tokens_mixed_content() -> None:
    """混合 CJK + 拉丁文分别计数。"""
    text = "Hello 你好 world 世界"
    cjk_chars = "你好世界"
    latin_len = len(text) - len(cjk_chars)
    expected = max(1, int(len(cjk_chars) / 1.5 + latin_len / 4))
    assert estimate_tokens(text) == expected


def test_estimate_tokens_minimum_one() -> None:
    """单个字符至少返回 1。"""
    assert estimate_tokens("a") >= 1
    assert estimate_tokens("你") >= 1


def test_estimate_tokens_long_text() -> None:
    """长文本估算值合理增长。"""
    # 用足够长的文本避免 max(1, ...) 的 clamp 干扰
    short = estimate_tokens("Hello world test")
    long = estimate_tokens("Hello world test" * 10)
    assert long > short
    assert long == short * 10  # 纯拉丁，线性缩放


# --- estimate_session_tokens ---


def test_estimate_session_tokens_empty() -> None:
    """空 session 返回 0。"""
    assert estimate_session_tokens(_session()) == 0


def test_estimate_session_tokens_history_only() -> None:
    """只有 history 时正确估算。"""
    s = _session(history=[
        ChatMessage(role="user", content="Hello"),
        ChatMessage(role="assistant", content="Hi there"),
    ])
    expected = estimate_tokens("Hello") + estimate_tokens("Hi there")
    assert estimate_session_tokens(s) == expected


def test_estimate_session_tokens_includes_summary() -> None:
    """summary 和 history 都计入。"""
    s = _session(
        history=[ChatMessage(role="user", content="Hello")],
        summary="This is a summary",
    )
    expected = estimate_tokens("This is a summary") + estimate_tokens("Hello")
    assert estimate_session_tokens(s) == expected


def test_estimate_session_tokens_extra_text() -> None:
    """extra_text（即将进入的用户消息）计入总量。"""
    s = _session(history=[ChatMessage(role="user", content="Hi")])
    base = estimate_tokens("Hi")
    extra = estimate_tokens("A long incoming message")
    assert estimate_session_tokens(s, extra_text="A long incoming message") == base + extra
