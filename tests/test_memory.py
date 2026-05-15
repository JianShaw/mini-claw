"""Memory store and lifecycle tests."""

from __future__ import annotations

from datetime import date

from claw.memory import DailyMemoryStore, LongTermMemoryStore, MemoryManager
from claw.types import ChatMessage, Session


def _session(history: list[ChatMessage] | None = None) -> Session:
    return Session(
        session_id="sess_test",
        session_key="local:app:user",
        channel="local",
        account_id="app",
        peer_id="user",
        sender_id="user",
        agent_id="default",
        history=history or [],
    )


def test_daily_memory_store_uses_flat_date_path(tmp_path) -> None:
    store = DailyMemoryStore(tmp_path)
    day = date(2026, 5, 14)

    store.write(day, "# Daily\n")

    assert store.path_for(day) == tmp_path / "daily" / "2026-05-14.md"
    assert store.read(day) == "# Daily\n"


def test_long_term_memory_store_uses_memory_md(tmp_path) -> None:
    store = LongTermMemoryStore(tmp_path)

    store.write("# Memory\n")

    assert store.path == tmp_path / "MEMORY.md"
    assert store.read() == "# Memory\n"


async def test_memory_manager_updates_daily_every_three_user_messages(tmp_path) -> None:
    manager = MemoryManager(
        tmp_path,
        today_provider=lambda: date(2026, 5, 14),
        update_every=3,
    )
    session = _session([
        ChatMessage(role="user", content="first"),
        ChatMessage(role="assistant", content="a1"),
        ChatMessage(role="user", content="second"),
        ChatMessage(role="assistant", content="a2"),
    ])

    assert await manager.maybe_update_daily(session) is False
    assert manager.daily_store.read(date(2026, 5, 14)) == ""

    session.history.extend([
        ChatMessage(role="user", content="希望 daily memory 用固定路径"),
        ChatMessage(role="assistant", content="ok"),
    ])

    assert await manager.maybe_update_daily(session) is True
    daily = manager.daily_store.read(date(2026, 5, 14))
    assert "# 2026-05-14 Daily Memory" in daily
    assert "Long-Term Candidates" in daily
    assert "希望 daily memory 用固定路径" in daily


async def test_memory_manager_force_update_daily(tmp_path) -> None:
    manager = MemoryManager(tmp_path, today_provider=lambda: date(2026, 5, 14))
    session = _session([
        ChatMessage(role="user", content="only one message"),
        ChatMessage(role="assistant", content="reply"),
    ])

    assert await manager.force_update_daily(session) is True
    assert "only one message" in manager.daily_store.read(date(2026, 5, 14))


async def test_memory_manager_distills_candidates_to_long_term(tmp_path) -> None:
    manager = MemoryManager(tmp_path, today_provider=lambda: date(2026, 5, 14))
    manager.daily_store.write(date(2026, 5, 14), """# Daily

## Long-Term Candidates
- User prefers finishing design before implementation.
- User is building Mini Claw memory.
""")

    result = await manager.distill_daily_to_long_term()

    assert result.added == 2
    long_memory = manager.long_store.read()
    assert "# Memory" in long_memory
    assert "User prefers finishing design before implementation." in long_memory
    assert "User is building Mini Claw memory." in long_memory

    result = await manager.distill_daily_to_long_term()
    assert result.added == 0
    assert manager.long_store.read().count("User is building Mini Claw memory.") == 1


async def test_memory_manager_build_context_reads_long_and_daily(tmp_path) -> None:
    manager = MemoryManager(tmp_path, today_provider=lambda: date(2026, 5, 14))
    manager.long_store.write("# Memory\n- Stable fact\n")
    manager.daily_store.write(date(2026, 5, 14), "# Daily\n- Today fact\n")

    context = await manager.build_context()

    assert "[Memory Context]" in context
    assert "Stable fact" in context
    assert "Today fact" in context
