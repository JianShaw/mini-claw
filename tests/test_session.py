"""会话模块测试：session_key 生成、Session 创建、内存存储存取。"""

from __future__ import annotations

import asyncio

from claw.session import InMemorySessionStore, build_session_key, create_session
from claw.types import InboundMessage


def _msg(**overrides: object) -> InboundMessage:
    """构造 InboundMessage 的辅助函数，支持覆盖任意字段。"""
    defaults = dict(
        channel="local",
        account_id="app",
        peer_id="user",
        sender_id="user",
        message_id="1",
        text="hi",
        timestamp=0,
        message_type="text",
        raw=None,
    )
    defaults.update(overrides)
    return InboundMessage(**defaults)  # type: ignore[arg-type]


def test_build_session_key_uses_channel_account_peer() -> None:
    """session_key 格式应为 channel:account_id:peer_id。"""
    msg = _msg(channel="feishu", account_id="bot-1", peer_id="user-42")
    assert build_session_key(msg) == "feishu:bot-1:user-42"


def test_create_session_copies_message_routing_fields() -> None:
    """创建的 Session 应复制消息的路由字段。"""
    msg = _msg(channel="local", account_id="app", peer_id="u1", sender_id="u1")
    session = create_session(msg)
    assert session.channel == "local"
    assert session.account_id == "app"
    assert session.peer_id == "u1"
    assert session.sender_id == "u1"


def test_create_session_uses_stable_session_key_and_generated_session_id() -> None:
    """同一消息创建多次：session_key 相同，但 session_id 每次不同。"""
    msg = _msg(channel="local", account_id="app", peer_id="u1")
    s1 = create_session(msg)
    s2 = create_session(msg)
    assert s1.session_key == s2.session_key
    assert s1.session_id != s2.session_id
    assert s1.session_id.startswith("sess_")


async def test_in_memory_session_store_returns_saved_session() -> None:
    """存入的 Session 能通过 session_key 取回同一个对象。"""
    store = InMemorySessionStore()
    msg = _msg(peer_id="u1")
    session = create_session(msg)
    await store.save(session)
    result = await store.get(session.session_key)
    assert result is session


async def test_in_memory_session_store_returns_none_for_missing_session() -> None:
    """查找不存在的 session_key 应返回 None。"""
    store = InMemorySessionStore()
    result = await store.get("nonexistent")
    assert result is None
