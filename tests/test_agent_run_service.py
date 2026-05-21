"""AgentRunService 单元测试：验证 session 加载、消息构建、压缩、profile 注入。"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from claw.scheduler.agent_run import AgentRunService
from claw.scheduler.types import AgentRun
from claw.session import InMemorySessionStore
from claw.session import create_session_from_identity
from claw.types import AgentReply, Session


def _make_session(session_id: str = "sess_test", agent_id: str = "ag_default") -> Session:
    """构造测试用 Session，手动设置 session_id。"""
    from claw.session import build_peer_key
    session = create_session_from_identity(
        channel="web", account_id="default", peer_id="sched:test",
        sender_id="scheduler", agent_id=agent_id,
    )
    # 覆盖 session_id 为固定值（create_session_from_identity 会生成随机的）
    object.__setattr__(session, "session_id", session_id)
    return session


def _make_run(**overrides) -> AgentRun:
    defaults = dict(
        session_id="sess_test",
        agent_id="ag_default",
        peer_key="web:default:sched:test",
        prompt="测试提示",
        task_name="test_task",
    )
    defaults.update(overrides)
    return AgentRun(**defaults)


@pytest.fixture
def session_store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def mock_runner() -> AsyncMock:
    runner = AsyncMock()
    runner.run = AsyncMock(return_value=AgentReply(text="回复内容"))
    return runner


@pytest.fixture
def service(mock_runner, session_store) -> AgentRunService:
    return AgentRunService(
        agent_runner=mock_runner,
        session_store=session_store,
    )


# ---- 核心执行流程 ----


class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_calls_runner_with_session_and_message(
        self, service, mock_runner, session_store
    ):
        session = _make_session()
        await session_store.save(session)

        run = _make_run()
        reply = await service.execute(run)

        assert reply.text == "回复内容"
        mock_runner.run.assert_awaited_once()
        session_arg, msg_arg = mock_runner.run.await_args.args
        assert session_arg.session_id == "sess_test"
        assert msg_arg.text == "测试提示"
        assert msg_arg.metadata["scheduled"] is True
        assert msg_arg.metadata["task_name"] == "test_task"

    @pytest.mark.asyncio
    async def test_execute_saves_session_after_run(
        self, service, mock_runner, session_store
    ):
        session = _make_session()
        await session_store.save(session)

        await service.execute(_make_run())

        # 验证 session 被保存（runner 可能修改了 history）
        saved = await session_store.get_by_id("sess_test")
        assert saved is not None

    @pytest.mark.asyncio
    async def test_execute_raises_on_missing_session(self, service):
        run = _make_run(session_id="nonexistent")
        with pytest.raises(ValueError, match="Session not found"):
            await service.execute(run)


# ---- 消息构建 ----


class TestBuildInboundMessage:
    def test_splits_peer_key_correctly(self, service):
        run = _make_run(peer_key="web:default:sched:remind")
        msg = service._build_inbound_message(run)
        assert msg.channel == "web"
        assert msg.account_id == "default"
        assert msg.peer_id == "sched:remind"

    def test_handles_short_peer_key(self, service):
        run = _make_run(peer_key="local")
        msg = service._build_inbound_message(run)
        assert msg.channel == "local"
        assert msg.account_id == "app"
        assert msg.peer_id == "user"

    def test_metadata_includes_scheduled_flag(self, service):
        run = _make_run()
        msg = service._build_inbound_message(run)
        assert msg.metadata["scheduled"] is True
        assert msg.metadata["task_name"] == "test_task"


# ---- Agent profile 注入 ----


class TestAgentRuntimeProfile:
    @pytest.mark.asyncio
    async def test_injects_profile_when_resolver_present(
        self, mock_runner, session_store
    ):
        from dataclasses import dataclass

        @dataclass
        class FakeProfile:
            model: str = "test-model"
            temperature: float = 0.7

        resolver = MagicMock()
        resolver.resolve = MagicMock(return_value=FakeProfile())

        service = AgentRunService(
            agent_runner=mock_runner,
            session_store=session_store,
            agent_resolver=resolver,
        )

        session = _make_session(agent_id="ag_expert")
        await session_store.save(session)

        await service.execute(_make_run())

        saved = await session_store.get_by_id("sess_test")
        assert "agent_runtime_profile" in saved.metadata
        assert saved.metadata["agent_runtime_profile"]["model"] == "test-model"

    @pytest.mark.asyncio
    async def test_removes_profile_when_no_resolver(
        self, service, session_store
    ):
        session = _make_session()
        session.metadata["agent_runtime_profile"] = {"old": "data"}
        await session_store.save(session)

        await service.execute(_make_run())

        saved = await session_store.get_by_id("sess_test")
        assert "agent_runtime_profile" not in saved.metadata


# ---- 自动压缩 ----


class TestAutoCompress:
    @pytest.mark.asyncio
    async def test_compresses_when_needed(
        self, mock_runner, session_store
    ):
        compressor = AsyncMock()
        compressor.should_compress = MagicMock(return_value=True)
        compressor.compress = AsyncMock(return_value="压缩摘要")

        service = AgentRunService(
            agent_runner=mock_runner,
            session_store=session_store,
            compressor=compressor,
        )

        session = _make_session()
        await session_store.save(session)

        await service.execute(_make_run())

        compressor.should_compress.assert_called_once()
        compressor.compress.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_compress_when_not_needed(
        self, mock_runner, session_store
    ):
        compressor = AsyncMock()
        compressor.should_compress = MagicMock(return_value=False)

        service = AgentRunService(
            agent_runner=mock_runner,
            session_store=session_store,
            compressor=compressor,
        )

        session = _make_session()
        await session_store.save(session)

        await service.execute(_make_run())

        compressor.should_compress.assert_called_once()
        compressor.compress.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_compressor_skips_gracefully(
        self, service, session_store
    ):
        session = _make_session()
        await session_store.save(session)

        # 不应抛异常
        await service.execute(_make_run())


# ---- Memory 更新 ----


class TestMemoryUpdate:
    @pytest.mark.asyncio
    async def test_updates_memory_when_manager_present(
        self, mock_runner, session_store
    ):
        mm = AsyncMock()
        service = AgentRunService(
            agent_runner=mock_runner,
            session_store=session_store,
            memory_manager=mm,
        )

        session = _make_session()
        await session_store.save(session)

        await service.execute(_make_run())

        mm.maybe_update_daily.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_memory_failure_does_not_crash(
        self, mock_runner, session_store
    ):
        mm = AsyncMock()
        mm.maybe_update_daily = AsyncMock(side_effect=RuntimeError("disk full"))

        service = AgentRunService(
            agent_runner=mock_runner,
            session_store=session_store,
            memory_manager=mm,
        )

        session = _make_session()
        await session_store.save(session)

        # 不应抛异常，memory 更新失败是 best-effort
        reply = await service.execute(_make_run())
        assert reply.text == "回复内容"
