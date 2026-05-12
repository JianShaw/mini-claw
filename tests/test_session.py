"""会话模块测试：session_key 生成、Session 创建、内存存储的多会话操作。"""

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


# --- InMemorySessionStore 基础测试 ---


async def test_in_memory_store_get_returns_active_session() -> None:
    """get(peer_key) 返回当前活跃的 session。"""
    store = InMemorySessionStore()
    msg = _msg(peer_id="u1")
    s1 = create_session(msg)
    await store.save(s1)
    result = await store.get("local:app:u1")
    assert result is s1


async def test_in_memory_store_get_returns_none_when_no_active() -> None:
    """没有 session 时 get 返回 None。"""
    store = InMemorySessionStore()
    result = await store.get("local:app:user")
    assert result is None


async def test_in_memory_store_save_auto_sets_active() -> None:
    """首次 save 自动设为活跃。"""
    store = InMemorySessionStore()
    s1 = create_session(_msg(peer_id="u1"))
    await store.save(s1)
    assert await store.get_active("local:app:u1") is s1


# --- get_by_id ---


async def test_in_memory_store_get_by_id() -> None:
    """按 session_id 查找 session。"""
    store = InMemorySessionStore()
    s1 = create_session(_msg(peer_id="u1"))
    await store.save(s1)
    assert await store.get_by_id(s1.session_id) is s1


async def test_in_memory_store_get_by_id_not_found() -> None:
    """不存在的 session_id 返回 None。"""
    store = InMemorySessionStore()
    assert await store.get_by_id("nonexistent") is None


# --- list_sessions ---


async def test_in_memory_store_list_sessions() -> None:
    """列出 peer 下的所有 session。"""
    store = InMemorySessionStore()
    s1 = create_session(_msg(peer_id="u1"))
    s2 = create_session(_msg(peer_id="u1"))
    await store.save(s1)
    await store.save(s2)
    sessions = await store.list_sessions("local:app:u1")
    assert len(sessions) == 2
    ids = {s.session_id for s in sessions}
    assert ids == {s1.session_id, s2.session_id}


async def test_in_memory_store_list_sessions_empty() -> None:
    """peer 下没有 session 时返回空列表。"""
    store = InMemorySessionStore()
    sessions = await store.list_sessions("local:app:user")
    assert sessions == []


# --- set_active / get_active ---


async def test_in_memory_store_set_and_get_active() -> None:
    """set_active 后 get_active 返回指定 session。"""
    store = InMemorySessionStore()
    s1 = create_session(_msg(peer_id="u1"))
    s2 = create_session(_msg(peer_id="u1"))
    await store.save(s1)
    await store.save(s2)
    await store.set_active("local:app:u1", s2.session_id)
    assert await store.get_active("local:app:u1") is s2
    # get(peer_key) 也返回活跃的
    assert await store.get("local:app:u1") is s2


# --- delete ---


async def test_in_memory_store_delete_removes_session() -> None:
    """delete 后 session 不再可查。"""
    store = InMemorySessionStore()
    s1 = create_session(_msg(peer_id="u1"))
    await store.save(s1)
    await store.delete(s1.session_id)
    assert await store.get_by_id(s1.session_id) is None


async def test_in_memory_store_delete_active_switches_to_another() -> None:
    """删除活跃 session 时自动切换到同 peer 的另一个。"""
    store = InMemorySessionStore()
    s1 = create_session(_msg(peer_id="u1"))
    s2 = create_session(_msg(peer_id="u1"))
    await store.save(s1)
    await store.save(s2)
    # s2 是活跃的（最后 save 的）
    await store.set_active("local:app:u1", s1.session_id)
    # 删除活跃的 s1
    await store.delete(s1.session_id)
    # 活跃应自动切到 s2
    active = await store.get_active("local:app:u1")
    assert active is s2


async def test_in_memory_store_delete_last_session_clears_active() -> None:
    """删除 peer 下唯一 session 后 get_active 返回 None。"""
    store = InMemorySessionStore()
    s1 = create_session(_msg(peer_id="u1"))
    await store.save(s1)
    await store.delete(s1.session_id)
    assert await store.get_active("local:app:u1") is None
    assert await store.get("local:app:u1") is None
