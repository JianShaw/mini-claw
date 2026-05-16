"""预定义任务测试：daily distill、periodic update、idle compact。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from claw.scheduler.context import TaskContext
from claw.scheduler.tasks import (
    daily_memory_distill,
    idle_auto_compact,
    periodic_memory_update,
)
from claw.types import Session


def _make_session(peer_key: str = "local:app:user") -> Session:
    return Session(
        session_id="sess_test",
        session_key=peer_key,
        channel="local",
        account_id="app",
        peer_id="user",
        sender_id="user",
        agent_id="default",
    )


def _mock_context(
    *,
    has_memory: bool = True,
    has_gateway: bool = True,
    sessions: list[tuple[str, Session]] | None = None,
) -> TaskContext:
    store = AsyncMock()
    if sessions:
        store.list_peer_keys = AsyncMock(return_value=[pk for pk, _ in sessions])

        async def _get_active(pk: str) -> Session | None:
            for p, s in sessions:
                if p == pk:
                    return s
            return None

        store.get_active = _get_active
    else:
        store.list_peer_keys = AsyncMock(return_value=[])
        store.get_active = AsyncMock(return_value=None)

    memory = None
    if has_memory:
        memory = MagicMock()
        memory.distill_daily_to_long_term = AsyncMock()
        memory.distill_daily_to_long_term.return_value = MagicMock(added=3)
        memory.maybe_update_daily = AsyncMock(return_value=True)

    gateway = None
    if has_gateway:
        gateway = AsyncMock()
        gateway.compact_session = AsyncMock(return_value="summary text")

    ctx = TaskContext(
        _session_store=store,
        _memory_manager=memory,
        _gateway=gateway,
    )
    return ctx


# --- daily_memory_distill ---


async def test_daily_distill_success() -> None:
    ctx = _mock_context()
    result = await daily_memory_distill(ctx)
    assert result.success
    assert "3 item(s)" in result.message


async def test_daily_distill_no_memory_manager() -> None:
    ctx = _mock_context(has_memory=False)
    result = await daily_memory_distill(ctx)
    assert not result.success
    assert "No memory_manager" in result.error


# --- periodic_memory_update ---


async def test_periodic_update_with_sessions() -> None:
    sessions = [("local:app:user", _make_session())]
    ctx = _mock_context(sessions=sessions)
    result = await periodic_memory_update(ctx)
    assert result.success
    assert "Updated 1/1" in result.message


async def test_periodic_update_no_sessions() -> None:
    ctx = _mock_context()
    result = await periodic_memory_update(ctx)
    assert result.success
    assert "Skipped" in result.message


async def test_periodic_update_multiple_sessions() -> None:
    sessions = [
        ("local:app:user1", _make_session("local:app:user1")),
        ("local:app:user2", _make_session("local:app:user2")),
    ]
    ctx = _mock_context(sessions=sessions)
    result = await periodic_memory_update(ctx)
    assert result.success
    assert "Updated 2/2" in result.message


# --- idle_auto_compact ---


async def test_idle_compact_success() -> None:
    ctx = _mock_context(
        sessions=[("local:app:user", _make_session())],
        has_gateway=True,
    )
    # 模拟 event payload
    ctx._event_payloads["session_activity"] = [{"peer_key": "local:app:user"}]
    result = await idle_auto_compact(ctx)
    assert result.success
    assert "Compacted" in result.message


async def test_idle_compact_no_peer_from_event() -> None:
    ctx = _mock_context()
    result = await idle_auto_compact(ctx)
    assert result.success
    assert "Skipped" in result.message


async def test_idle_compact_no_active_session() -> None:
    ctx = _mock_context(has_gateway=True)
    ctx._event_payloads["session_activity"] = [{"peer_key": "local:app:gone"}]
    result = await idle_auto_compact(ctx)
    assert result.success
    assert "Skipped" in result.message


async def test_idle_compact_nothing_to_compact() -> None:
    ctx = _mock_context(
        sessions=[("local:app:user", _make_session())],
        has_gateway=True,
    )
    ctx._gateway.compact_session = AsyncMock(return_value=None)
    ctx._event_payloads["session_activity"] = [{"peer_key": "local:app:user"}]
    result = await idle_auto_compact(ctx)
    assert result.success
    assert "Nothing to compact" in result.message
