"""测试 claw/storage/session_store 模块 — SqliteSessionStore。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from claw.storage.session_store import SqliteSessionStore
from claw.storage.sqlite import get_connection, init_db
from claw.types import ChatMessage, Session


def _make_session(
    session_id: str = "sess_001",
    session_key: str = "web:default:web",
    agent_id: str = "default-agent",
    history: list[ChatMessage] | None = None,
    **overrides,
) -> Session:
    """构造测试用 Session。"""
    base = dict(
        session_id=session_id,
        session_key=session_key,
        channel="web",
        account_id="default",
        peer_id="web",
        sender_id="web",
        agent_id=agent_id,
        history=history or [],
    )
    base.update(overrides)
    return Session(**base)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = get_connection(tmp_path / "test.sqlite")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def store(conn: sqlite3.Connection) -> SqliteSessionStore:
    return SqliteSessionStore(conn)


# ---- 基础 CRUD ----


class TestSaveAndGet:
    async def test_save_and_get_by_id(self, store: SqliteSessionStore) -> None:
        msgs = [
            ChatMessage(role="user", content="hello"),
            ChatMessage(role="assistant", content="hi there"),
        ]
        session = _make_session(history=msgs)
        await store.save(session)

        loaded = await store.get_by_id("sess_001")
        assert loaded is not None
        assert loaded.session_id == "sess_001"
        assert loaded.agent_id == "default-agent"
        assert len(loaded.history) == 2
        assert loaded.history[0].content == "hello"
        assert loaded.history[1].content == "hi there"

    async def test_save_appends_new_messages_only(
        self, store: SqliteSessionStore, conn: sqlite3.Connection
    ) -> None:
        session = _make_session(
            history=[ChatMessage(role="user", content="first")]
        )
        await store.save(session)

        # 追加一条消息
        session.history.append(ChatMessage(role="assistant", content="second"))
        await store.save(session)

        # 验证 session_messages 表只有 2 行（不重复追写 first）
        count = conn.execute(
            "SELECT COUNT(*) FROM session_messages WHERE session_id = ?",
            ("sess_001",),
        ).fetchone()[0]
        assert count == 2

    async def test_save_auto_sets_active(self, store: SqliteSessionStore) -> None:
        await store.save(_make_session())
        active = await store.get_active("web:default:web")
        assert active is not None
        assert active.session_id == "sess_001"


class TestGetActive:
    async def test_get_active_returns_active_session(
        self, store: SqliteSessionStore
    ) -> None:
        await store.save(
            _make_session(session_id="sess_001", history=[])
        )
        active = await store.get_active("web:default:web")
        assert active is not None
        assert active.session_id == "sess_001"

    async def test_get_active_returns_none_when_empty(
        self, store: SqliteSessionStore
    ) -> None:
        active = await store.get_active("web:default:web")
        assert active is None

    async def test_get_is_alias_for_get_active(
        self, store: SqliteSessionStore
    ) -> None:
        await store.save(_make_session())
        result = await store.get("web:default:web")
        assert result is not None
        assert result.session_id == "sess_001"


class TestSetActive:
    async def test_set_active_switches_session(
        self, store: SqliteSessionStore
    ) -> None:
        await store.save(_make_session(session_id="sess_001"))
        await store.save(
            _make_session(session_id="sess_002")
        )
        await store.set_active("web:default:web", "sess_002")

        active = await store.get_active("web:default:web")
        assert active is not None
        assert active.session_id == "sess_002"


class TestListSessions:
    async def test_list_sessions(self, store: SqliteSessionStore) -> None:
        await store.save(
            _make_session(session_id="sess_001", summary="first")
        )
        await store.save(
            _make_session(session_id="sess_002", summary="second")
        )

        sessions = await store.list_sessions("web:default:web")
        assert len(sessions) == 2
        ids = {s.session_id for s in sessions}
        assert ids == {"sess_001", "sess_002"}

    async def test_list_sessions_empty(self, store: SqliteSessionStore) -> None:
        sessions = await store.list_sessions("web:default:web")
        assert sessions == []

    async def test_list_sessions_no_history(
        self, store: SqliteSessionStore
    ) -> None:
        msgs = [ChatMessage(role="user", content="hello")]
        await store.save(_make_session(history=msgs))

        sessions = await store.list_sessions("web:default:web")
        assert len(sessions) == 1
        # list_sessions 不加载 history（轻量查询）
        assert sessions[0].history == []


class TestDelete:
    async def test_delete_removes_session(
        self, store: SqliteSessionStore, conn: sqlite3.Connection
    ) -> None:
        msgs = [ChatMessage(role="user", content="hello")]
        await store.save(_make_session(history=msgs))
        await store.delete("sess_001")

        loaded = await store.get_by_id("sess_001")
        assert loaded is None

        # CASCADE 删消息
        count = conn.execute(
            "SELECT COUNT(*) FROM session_messages WHERE session_id = ?",
            ("sess_001",),
        ).fetchone()[0]
        assert count == 0

    async def test_delete_active_switches_to_another(
        self, store: SqliteSessionStore
    ) -> None:
        await store.save(
            _make_session(session_id="sess_001", summary="first")
        )
        await store.save(
            _make_session(session_id="sess_002", summary="second")
        )
        await store.set_active("web:default:web", "sess_001")

        await store.delete("sess_001")

        active = await store.get_active("web:default:web")
        assert active is not None
        assert active.session_id == "sess_002"

    async def test_delete_last_session_clears_active(
        self, store: SqliteSessionStore
    ) -> None:
        await store.save(_make_session())
        await store.delete("sess_001")

        active = await store.get_active("web:default:web")
        assert active is None

    async def test_delete_nonexistent_is_noop(
        self, store: SqliteSessionStore
    ) -> None:
        # 不应抛异常
        await store.delete("nonexistent")


class TestListPeerKeys:
    async def test_list_peer_keys(
        self, store: SqliteSessionStore
    ) -> None:
        await store.save(_make_session(session_key="web:default:web"))
        await store.save(
            _make_session(
                session_id="sess_002",
                session_key="cli:user1:peer1",
                channel="cli",
                account_id="user1",
                peer_id="peer1",
            )
        )
        keys = await store.list_peer_keys()
        assert set(keys) == {"web:default:web", "cli:user1:peer1"}


# ---- 消息序列化 ----


class TestMessageSerialization:
    async def test_tool_call_message(
        self, store: SqliteSessionStore
    ) -> None:
        msgs = [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[{"id": "tc_1", "function": {"name": "read_file"}}],
            )
        ]
        await store.save(_make_session(history=msgs))

        loaded = await store.get_by_id("sess_001")
        assert loaded is not None
        assert loaded.history[0].tool_calls == [
            {"id": "tc_1", "function": {"name": "read_file"}}
        ]

    async def test_tool_result_message(
        self, store: SqliteSessionStore
    ) -> None:
        msgs = [
            ChatMessage(
                role="tool",
                content="file content here",
                tool_call_id="tc_1",
                tool_name="read_file",
            )
        ]
        await store.save(_make_session(history=msgs))

        loaded = await store.get_by_id("sess_001")
        assert loaded is not None
        msg = loaded.history[0]
        assert msg.tool_call_id == "tc_1"
        assert msg.tool_name == "read_file"

    async def test_reasoning_content(
        self, store: SqliteSessionStore
    ) -> None:
        msgs = [
            ChatMessage(
                role="assistant",
                content="answer",
                reasoning_content="thinking...",
            )
        ]
        await store.save(_make_session(history=msgs))

        loaded = await store.get_by_id("sess_001")
        assert loaded is not None
        assert loaded.history[0].reasoning_content == "thinking..."


# ---- History Offset（压缩场景）----


class TestHistoryOffset:
    async def test_offset_prevents_old_messages_reappearing(
        self, store: SqliteSessionStore
    ) -> None:
        # 初始：3 条消息
        msgs = [
            ChatMessage(role="user", content="q1"),
            ChatMessage(role="assistant", content="a1"),
            ChatMessage(role="user", content="q2"),
        ]
        session = _make_session(history=msgs)
        await store.save(session)

        # 模拟压缩：offset=2，history 缩减为最后 1 条
        session.history_offset = 2
        session.history = [ChatMessage(role="user", content="q2")]
        session.summary = "compressed summary"
        await store.save(session)

        # 重新加载：只应拿到 offset 之后的 1 条消息
        loaded = await store.get_by_id("sess_001")
        assert loaded is not None
        assert loaded.history_offset == 2
        assert len(loaded.history) == 1
        assert loaded.history[0].content == "q2"
        assert loaded.summary == "compressed summary"

    async def test_append_new_messages_after_compact(
        self, store: SqliteSessionStore, conn: sqlite3.Connection
    ) -> None:
        # 初始 2 条 → 压缩 → 追加 1 条
        msgs = [
            ChatMessage(role="user", content="q1"),
            ChatMessage(role="assistant", content="a1"),
        ]
        session = _make_session(history=msgs)
        await store.save(session)

        # 压缩
        session.history_offset = 2
        session.history = []
        session.summary = "old"
        await store.save(session)

        # 追加新消息
        session.history.append(ChatMessage(role="user", content="q2"))
        session.history.append(ChatMessage(role="assistant", content="a2"))
        await store.save(session)

        # 总消息行数 = 2 (原始) + 2 (新) = 4
        count = conn.execute(
            "SELECT COUNT(*) FROM session_messages WHERE session_id = ?",
            ("sess_001",),
        ).fetchone()[0]
        assert count == 4

        # 加载后只看到 offset 之后的 2 条
        loaded = await store.get_by_id("sess_001")
        assert loaded is not None
        assert len(loaded.history) == 2
        assert loaded.history[0].content == "q2"


# ---- 元数据保留 ----


class TestMetadata:
    async def test_created_at_preserved_on_update(
        self, store: SqliteSessionStore, conn: sqlite3.Connection
    ) -> None:
        await store.save(_make_session())
        created_at_before = conn.execute(
            "SELECT created_at FROM sessions WHERE session_id = ?",
            ("sess_001",),
        ).fetchone()["created_at"]

        # 再次 save（更新）
        session = _make_session(summary="updated")
        await store.save(session)

        created_at_after = conn.execute(
            "SELECT created_at FROM sessions WHERE session_id = ?",
            ("sess_001",),
        ).fetchone()["created_at"]

        assert created_at_before == created_at_after

    async def test_metadata_json_round_trip(
        self, store: SqliteSessionStore
    ) -> None:
        session = _make_session(
            metadata={"channel": "web", "custom_key": "value"}
        )
        await store.save(session)

        loaded = await store.get_by_id("sess_001")
        assert loaded is not None
        assert loaded.metadata["custom_key"] == "value"
