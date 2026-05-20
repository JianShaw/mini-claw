"""测试 RuntimeContextBuilder + ContextBuildingAgentRunner wrapper。"""

from __future__ import annotations

import pytest

from claw.agent_runtime.context import RuntimeContextBuilder
from claw.agent_runtime.wrapper import ContextBuildingAgentRunner
from claw.skills.registry import SkillsRegistry
from claw.skills.types import Skill, SkillMeta
from claw.types import AgentReply, InboundMessage, Session, StreamChunk


def _make_session(**kwargs) -> Session:
    defaults = {
        "session_id": "test-sess",
        "session_key": "web:default:web",
        "channel": "web",
        "account_id": "default",
        "peer_id": "web",
        "sender_id": "web",
        "agent_id": "default-agent",
    }
    defaults.update(kwargs)
    return Session(**defaults)


def _make_message(text: str = "hello", **kwargs) -> InboundMessage:
    defaults = {
        "channel": "web",
        "account_id": "default",
        "peer_id": "web",
        "sender_id": "web",
        "message_id": "msg-1",
        "text": text,
        "timestamp": 0,
        "message_type": "text",
        "raw": None,
    }
    defaults.update(kwargs)
    return InboundMessage(**defaults)


# --- Fake implementations ---

class _FakeMemoryManager:
    def __init__(self, context: str | None = "remembered context"):
        self._context = context
        self.build_context_calls: list = []

    async def build_context(self, message):
        self.build_context_calls.append(message)
        return self._context


class _FakeSkillsRegistry:
    def __init__(self, listing: str | None = "## Skills\n\n- **test**: test skill"):
        self._listing = listing

    def build_skills_listing(self, **kwargs):
        return self._listing


class _FakeEmptySkillsRegistry:
    def build_skills_listing(self, **kwargs):
        return None


class _SpyRunner:
    """Runner that records calls and returns canned replies."""
    def __init__(self):
        self.run_calls: list[tuple[Session, InboundMessage]] = []
        self.stream_calls: list[tuple[Session, InboundMessage]] = []

    async def run(self, session, message):
        self.run_calls.append((session, message))
        return AgentReply(text="fake reply")

    async def run_stream(self, session, message):
        self.stream_calls.append((session, message))
        yield StreamChunk(type="content", text="fake reply")


class TestRuntimeContextBuilder:
    @pytest.mark.asyncio
    async def test_injects_memory_context(self):
        mm = _FakeMemoryManager("user pref: likes pizza")
        builder = RuntimeContextBuilder(memory_manager=mm)
        session = _make_session()
        msg = _make_message("hello")

        await builder.build(session, msg)

        assert session.metadata["memory_context"] == "user pref: likes pizza"

    @pytest.mark.asyncio
    async def test_no_memory_manager_clears_context(self):
        builder = RuntimeContextBuilder(memory_manager=None)
        session = _make_session()
        session.metadata["memory_context"] = "old context"
        msg = _make_message()

        await builder.build(session, msg)

        assert "memory_context" not in session.metadata

    @pytest.mark.asyncio
    async def test_empty_context_clears_metadata(self):
        mm = _FakeMemoryManager(None)
        builder = RuntimeContextBuilder(memory_manager=mm)
        session = _make_session()
        session.metadata["memory_context"] = "old"
        msg = _make_message()

        await builder.build(session, msg)

        assert "memory_context" not in session.metadata

    @pytest.mark.asyncio
    async def test_injects_skills_listing(self):
        registry = _FakeSkillsRegistry("## Skills\n\n- **pdf**: Process PDF")
        builder = RuntimeContextBuilder(skills_registry=registry)
        session = _make_session()
        msg = _make_message()

        await builder.build(session, msg)

        assert "skills_listing" in session.metadata
        assert "pdf" in session.metadata["skills_listing"]

    @pytest.mark.asyncio
    async def test_no_skills_registry_clears_listing(self):
        builder = RuntimeContextBuilder(skills_registry=None)
        session = _make_session()
        session.metadata["skills_listing"] = "old listing"
        msg = _make_message()

        await builder.build(session, msg)

        assert "skills_listing" not in session.metadata

    @pytest.mark.asyncio
    async def test_empty_listing_clears_metadata(self):
        registry = _FakeEmptySkillsRegistry()
        builder = RuntimeContextBuilder(skills_registry=registry)
        session = _make_session()
        session.metadata["skills_listing"] = "old"
        msg = _make_message()

        await builder.build(session, msg)

        assert "skills_listing" not in session.metadata

    @pytest.mark.asyncio
    async def test_filters_by_enabled_skills_from_agent_profile(self):
        """有 agent_runtime_profile 的 enabled_skills 时过滤。"""
        registry = SkillsRegistry()
        registry.register(Skill(name="a", description="skill a", instructions="do a"))
        registry.register(Skill(name="b", description="skill b", instructions="do b"))

        builder = RuntimeContextBuilder(skills_registry=registry)
        session = _make_session()
        # 模拟 Gateway 注入的 agent_runtime_profile
        session.metadata["agent_runtime_profile"] = {"enabled_skills": ["a"]}
        msg = _make_message()

        await builder.build(session, msg)

        listing = session.metadata["skills_listing"]
        assert "**a**" in listing
        # 技能 b 被过滤，不应出现在 listing 中
        assert "**b**" not in listing


class TestContextBuildingAgentRunner:
    @pytest.mark.asyncio
    async def test_wrapper_calls_build_before_run(self):
        mm = _FakeMemoryManager("memory")
        builder = RuntimeContextBuilder(memory_manager=mm)
        spy = _SpyRunner()
        wrapper = ContextBuildingAgentRunner(spy, builder)

        session = _make_session()
        msg = _make_message("hi")

        reply = await wrapper.run(session, msg)

        # 验证 build 被调用了（memory_context 已注入）
        assert session.metadata["memory_context"] == "memory"
        # 验证 inner runner 被调用
        assert len(spy.run_calls) == 1
        assert reply.text == "fake reply"

    @pytest.mark.asyncio
    async def test_wrapper_skips_build_with_flag(self):
        mm = _FakeMemoryManager("should not appear")
        builder = RuntimeContextBuilder(memory_manager=mm)
        spy = _SpyRunner()
        wrapper = ContextBuildingAgentRunner(spy, builder)

        session = _make_session()
        msg = _make_message()
        # _full_compact 设置此标记防止上下文污染
        msg.metadata["skip_runtime_context"] = True

        await wrapper.run(session, msg)

        # 验证 build 被跳过
        assert "memory_context" not in session.metadata
        assert len(spy.run_calls) == 1

    @pytest.mark.asyncio
    async def test_wrapper_calls_build_before_stream(self):
        mm = _FakeMemoryManager("stream context")
        builder = RuntimeContextBuilder(memory_manager=mm)
        spy = _SpyRunner()
        wrapper = ContextBuildingAgentRunner(spy, builder)

        session = _make_session()
        msg = _make_message("hi")

        chunks = [c async for c in wrapper.run_stream(session, msg)]

        assert session.metadata["memory_context"] == "stream context"
        assert len(spy.stream_calls) == 1
        assert chunks[0].text == "fake reply"

    @pytest.mark.asyncio
    async def test_wrapper_skips_build_on_stream_with_flag(self):
        mm = _FakeMemoryManager("should NOT appear")
        builder = RuntimeContextBuilder(memory_manager=mm)
        spy = _SpyRunner()
        wrapper = ContextBuildingAgentRunner(spy, builder)

        session = _make_session()
        msg = _make_message()
        msg.metadata["skip_runtime_context"] = True

        _chunks = [c async for c in wrapper.run_stream(session, msg)]

        assert "memory_context" not in session.metadata
        assert len(spy.stream_calls) == 1

    @pytest.mark.asyncio
    async def test_wrapper_preserves_inner_reply(self):
        builder = RuntimeContextBuilder()
        spy = _SpyRunner()
        wrapper = ContextBuildingAgentRunner(spy, builder)

        reply = await wrapper.run(_make_session(), _make_message())

        assert reply.text == "fake reply"
