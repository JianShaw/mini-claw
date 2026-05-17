"""Memory store and lifecycle tests."""

from __future__ import annotations

import logging
from datetime import date

from claw.memory import (
    DailyMemoryStore,
    HybridMemorySearch,
    LongTermMemoryStore,
    MemoryChunk,
    MemoryManager,
    SQLiteMemoryVectorIndex,
)
from claw.types import ChatMessage, InboundMessage, Session


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


def _msg(text: str) -> InboundMessage:
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


class _FakeEmbeddingProvider:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_fake_vector(text) for text in texts]


class _FailingEmbeddingProvider:
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding unavailable")


def _fake_vector(text: str) -> list[float]:
    lowered = text.lower()
    if any(term in lowered for term in ("production", "deploy", "environment")):
        return [1.0, 0.0, 0.0]
    if any(term in text for term in ("生产", "部署", "环境")):
        return [1.0, 0.0, 0.0]
    if "quiet" in lowered or "dashboard" in lowered:
        return [0.0, 1.0, 0.0]
    return [0.0, 0.0, 1.0]


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


def test_hybrid_memory_search_prefers_relevant_chunks() -> None:
    searcher = HybridMemorySearch()
    chunks = [
        MemoryChunk("Long-Term Memory", "Ops", "Production server deploy environment is Linux."),
        MemoryChunk("Long-Term Memory", "Design", "Use quiet dashboard colors."),
        MemoryChunk("Today's Daily Memory", "Tasks", "Update API error handling."),
    ]

    results = searcher.search("deploy environment", chunks, top_k=2)

    assert results[0].chunk.text == "Production server deploy environment is Linux."
    assert all("quiet dashboard" not in result.chunk.text for result in results[:1])


def test_hybrid_memory_search_filters_zero_score_results() -> None:
    searcher = HybridMemorySearch()
    chunks = [
        MemoryChunk("Long-Term Memory", "Ops", "Production server deploy environment is Linux."),
        MemoryChunk("Long-Term Memory", "Design", "Use quiet dashboard colors."),
    ]

    results = searcher.search("完全无关的问题", chunks, top_k=2)

    assert results == []


def test_hybrid_memory_search_matches_chinese_memory() -> None:
    searcher = HybridMemorySearch()
    chunks = [
        MemoryChunk("Long-Term Memory", "Ops", "生产部署环境是 Linux。"),
        MemoryChunk("Long-Term Memory", "Design", "Dashboard UI should use quiet colors."),
    ]

    results = searcher.search("生产部署环境是什么？", chunks, top_k=2)

    assert results[0].chunk.text == "生产部署环境是 Linux。"
    assert all(result.score > 0.0 for result in results)


async def test_memory_manager_build_context_uses_hybrid_search_limit(tmp_path) -> None:
    manager = MemoryManager(
        tmp_path,
        today_provider=lambda: date(2026, 5, 14),
        memory_top_k=1,
    )
    manager.long_store.write("""# Memory
- Production server deploy environment is Linux.
- User prefers quiet dashboard colors.
""")
    manager.daily_store.write(date(2026, 5, 14), """# Daily
- API names should remain stable.
""")

    context = await manager.build_context(_msg("deploy environment"))

    assert "Memory search uses SQLite vector retrieval" in context
    assert "Production server deploy environment is Linux." in context
    assert "quiet dashboard colors" not in context


async def test_memory_manager_build_context_does_not_inject_zero_score_matches(tmp_path) -> None:
    manager = MemoryManager(
        tmp_path,
        today_provider=lambda: date(2026, 5, 14),
        memory_top_k=2,
        use_vector_index=False,
    )
    manager.long_store.write("""# Memory
- Production server deploy environment is Linux.
- User prefers quiet dashboard colors.
""")

    context = await manager.build_context(_msg("完全无关的问题"))

    assert "[Memory Context]" in context
    assert "Production server deploy environment is Linux." not in context
    assert "quiet dashboard colors" not in context
    assert "[Long-Term Memory]\n(empty)" in context


async def test_memory_manager_logs_hybrid_search_results(tmp_path, caplog) -> None:
    manager = MemoryManager(
        tmp_path,
        today_provider=lambda: date(2026, 5, 14),
        memory_top_k=1,
        use_vector_index=False,
    )
    manager.long_store.write("""# Memory
- Production server deploy environment is Linux.
- User prefers quiet dashboard colors.
""")

    with caplog.at_level(logging.DEBUG, logger="claw.memory.manager"):
        await manager.build_context(_msg("deploy environment"))

    messages = [record.getMessage() for record in caplog.records]
    assert any("memory retrieval query='deploy environment'" in msg for msg in messages)
    assert any("chunks=2 filtered=2 top_k=1 results=1" in msg for msg in messages)
    assert any("memory result #1" in msg for msg in messages)
    assert any("Production server deploy environment is Linux." in msg for msg in messages)
    assert any("memory context rendered chars=" in msg for msg in messages)


async def test_memory_manager_filters_daily_echo_chunks(tmp_path) -> None:
    manager = MemoryManager(
        tmp_path,
        today_provider=lambda: date(2026, 5, 17),
        memory_top_k=8,
    )
    manager.long_store.write("# Memory\n- Production deploy environment is Linux.\n")
    manager.daily_store.write(date(2026, 5, 17), """# Daily

## Recent User Intent
- What is the production deploy environment?

## Current Context
- Recent user topic: What is the production deploy environment?
- Recent assistant response: A long previous answer about production deployment architecture.

## Tool Results
- file_read: noisy design document mentioning production deploy environment.

## Decisions Today
- Production deploy verification should prefer long-term memory facts.
""")

    context = await manager.build_context(_msg("What is the production deploy environment?"))

    assert "Production deploy environment is Linux." in context
    assert "Production deploy verification should prefer long-term memory facts." in context
    assert "- What is the production deploy environment?" not in context
    assert "Recent user topic:" not in context
    assert "Recent assistant response:" not in context
    assert "file_read:" not in context


async def test_memory_manager_vector_search_matches_cross_language_memory(tmp_path) -> None:
    manager = MemoryManager(
        tmp_path,
        today_provider=lambda: date(2026, 5, 17),
        memory_top_k=2,
        embedding_provider=_FakeEmbeddingProvider(),
    )
    manager.long_store.write("""# Memory
- Production deploy environment is Linux.
- Dashboard UI should use quiet colors.
""")

    context = await manager.build_context(_msg("生产部署环境是什么？"))

    assert "Production deploy environment is Linux." in context
    assert "Dashboard UI should use quiet colors." not in context


async def test_memory_manager_vector_index_tracks_markdown_edits(tmp_path) -> None:
    manager = MemoryManager(
        tmp_path,
        today_provider=lambda: date(2026, 5, 17),
        memory_top_k=2,
        embedding_provider=_FakeEmbeddingProvider(),
    )
    manager.long_store.write("# Memory\n- Production deploy environment is Linux.\n")

    context = await manager.build_context(_msg("生产部署环境是什么？"))
    assert "Production deploy environment is Linux." in context

    manager.long_store.write("# Memory\n- Dashboard UI should use quiet colors.\n")

    context = await manager.build_context(_msg("生产部署环境是什么？"))
    assert "Production deploy environment is Linux." not in context


async def test_memory_manager_falls_back_when_vector_index_fails(tmp_path, caplog) -> None:
    manager = MemoryManager(
        tmp_path,
        today_provider=lambda: date(2026, 5, 17),
        memory_top_k=2,
        embedding_provider=_FailingEmbeddingProvider(),
    )
    manager.long_store.write("# Memory\n- Production deploy environment is Linux.\n")

    with caplog.at_level(logging.WARNING, logger="claw.memory.manager"):
        context = await manager.build_context(_msg("deploy environment"))

    assert "Production deploy environment is Linux." in context
    assert any("falling back to hybrid search" in record.getMessage() for record in caplog.records)


async def test_memory_manager_vector_search_filters_daily_noise(tmp_path) -> None:
    manager = MemoryManager(
        tmp_path,
        today_provider=lambda: date(2026, 5, 17),
        memory_top_k=8,
        embedding_provider=_FakeEmbeddingProvider(),
    )
    manager.long_store.write("# Memory\n- Production deploy environment is Linux.\n")
    manager.daily_store.write(date(2026, 5, 17), """# Daily

## Recent User Intent
- What is the production deploy environment?

## Current Context
- Recent assistant response: A noisy answer about production deploy environment.

## Decisions Today
- Production deploy verification should prefer long-term memory facts.
""")

    context = await manager.build_context(_msg("What is the production deploy environment?"))

    assert "Production deploy environment is Linux." in context
    assert "Production deploy verification should prefer long-term memory facts." in context
    assert "- What is the production deploy environment?" not in context
    assert "Recent assistant response:" not in context


# ------------------------------------------------------------------
# vec0 模式专项测试
# ------------------------------------------------------------------


def test_vec0_index_search_returns_cosine_results(tmp_path) -> None:
    """vec0 虚表 KNN 查询应返回与 cosine 相似度一致的结果。"""
    from claw.memory.vector_index import MemorySource, SQLiteMemoryVectorIndex

    index = SQLiteMemoryVectorIndex(tmp_path, _FakeEmbeddingProvider())
    source = MemorySource(
        path=tmp_path / "test.md",
        source="Long-Term Memory",
        markdown="# Memory\n- Production deploy environment is Linux.\n- Dashboard uses quiet colors.\n",
    )
    (tmp_path / "test.md").write_text("dummy", encoding="utf-8")

    results = index.search("生产部署环境", [source], top_k=2)

    assert len(results) >= 1
    assert results[0].chunk.text == "Production deploy environment is Linux."
    assert results[0].score > 0.0


def test_vec0_index_sync_detects_edits(tmp_path) -> None:
    """markdown 编辑后 vec0 索引应自动重建。"""
    from claw.memory.vector_index import MemorySource, SQLiteMemoryVectorIndex

    index = SQLiteMemoryVectorIndex(tmp_path, _FakeEmbeddingProvider())
    path = tmp_path / "test.md"
    path.write_text("v1", encoding="utf-8")

    source_v1 = MemorySource(path=path, source="Long-Term Memory", markdown="# Memory\n- Production deploy env.\n")
    index.search("deploy", [source_v1], top_k=2)
    results_v1 = index.search("deploy", [source_v1], top_k=2)
    assert any("Production" in r.chunk.text for r in results_v1)

    source_v2 = MemorySource(path=path, source="Long-Term Memory", markdown="# Memory\n- Dashboard uses quiet colors.\n")
    results_v2 = index.search("quiet dashboard", [source_v2], top_k=2)
    assert not any("Production" in r.chunk.text for r in results_v2)
    assert any("Dashboard" in r.chunk.text for r in results_v2)


def test_vec0_index_empty_query_returns_all(tmp_path) -> None:
    """空查询应返回所有 chunk，score=0.0。"""
    from claw.memory.vector_index import MemorySource, SQLiteMemoryVectorIndex

    index = SQLiteMemoryVectorIndex(tmp_path, _FakeEmbeddingProvider())
    path = tmp_path / "test.md"
    path.write_text("dummy", encoding="utf-8")
    source = MemorySource(path=path, source="Long-Term Memory", markdown="# Memory\n- Fact A.\n- Fact B.\n")

    results = index.search("", [source], top_k=5)

    assert len(results) == 2
    assert all(r.score == 0.0 for r in results)


def test_serialize_f32_roundtrip() -> None:
    """serialize_f32 应产生 sqlite-vec 兼容的 bytes 格式。"""
    import struct
    from claw.memory.vector_index import serialize_f32

    vec = [1.0, 0.5, -0.3, 0.0]
    packed = serialize_f32(vec)
    assert isinstance(packed, bytes)
    assert len(packed) == len(vec) * 4
    unpacked = list(struct.unpack(f"<{len(vec)}f", packed))
    for original, recovered in zip(vec, unpacked):
        assert abs(original - recovered) < 1e-6
