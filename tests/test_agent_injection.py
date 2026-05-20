"""测试 V1 Phase 3a: Session→Agent 布线 + system prompt 注入。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from collections.abc import AsyncIterator

import pytest

from claw.agent_runtime.resolver import AgentResolver
from claw.agent_runtime.store import SqliteAgentStore
from claw.agent_runtime.types import AgentConfig, RuntimeProfile
from claw.gateway import RuntimeGateway, SessionNotFoundError
from claw.session import InMemorySessionStore
from claw.storage.sqlite import get_connection, init_db
from claw.types import AgentReply, ChatMessage, InboundMessage, Session, StreamChunk


# ---- Test doubles ----

class EchoAgentRunner:
    """测试用 AgentRunner：回显消息 + profile 信息。"""

    async def run(self, session: Session, message: InboundMessage) -> AgentReply:
        profile = session.metadata.get("agent_runtime_profile")
        prompt = profile["system_prompt"] if profile else "(no profile)"
        return AgentReply(text=f"echo: {message.text} | prompt: {prompt}")

    async def run_stream(self, session: Session, message: InboundMessage) -> AsyncIterator[StreamChunk]:
        profile = session.metadata.get("agent_runtime_profile")
        prompt = profile["system_prompt"] if profile else "(no profile)"
        yield StreamChunk(type="content", text=f"echo: {message.text} | prompt: {prompt}")


class SilentDelivery:
    async def send(self, message: InboundMessage, reply: AgentReply) -> None:
        pass


def _web_message(text: str, session_id: str | None = None) -> InboundMessage:
    return InboundMessage(
        channel="web", account_id="default", peer_id="web", sender_id="web",
        message_id="msg_1", text=text, timestamp=1000, message_type="text",
        raw=None, metadata={"session_id": session_id} if session_id else {},
    )


def _cli_message(text: str) -> InboundMessage:
    return InboundMessage(
        channel="local", account_id="default", peer_id="cli", sender_id="cli",
        message_id="msg_1", text=text, timestamp=1000, message_type="text",
        raw=None,
    )


# peer identity 常量
_WEB_PK = "web:default:web"
_WEB_ID = dict(channel="web", account_id="default", peer_id="web", sender_id="web")
_CLI_PK = "local:default:cli"
_CLI_ID = dict(channel="local", account_id="default", peer_id="cli", sender_id="cli")


# ---- Fixtures ----

@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = get_connection(tmp_path / "test.sqlite")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def agent_store(conn: sqlite3.Connection) -> SqliteAgentStore:
    return SqliteAgentStore(conn)


@pytest.fixture
def agent_resolver(agent_store: SqliteAgentStore) -> AgentResolver:
    return AgentResolver(agent_store)


@pytest.fixture
def session_store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def gateway(session_store: InMemorySessionStore, agent_resolver: AgentResolver) -> RuntimeGateway:
    return RuntimeGateway(
        session_store=session_store,
        agent_runner=EchoAgentRunner(),
        delivery=SilentDelivery(),
        agent_resolver=agent_resolver,
    )


@pytest.fixture
def gateway_no_resolver(session_store: InMemorySessionStore) -> RuntimeGateway:
    return RuntimeGateway(
        session_store=session_store,
        agent_runner=EchoAgentRunner(),
        delivery=SilentDelivery(),
    )


# ---- Tests ----

class TestAgentRuntimeProfileInjection:
    @pytest.mark.asyncio
    async def test_profile_injected_with_default_agent(
        self, gateway: RuntimeGateway, agent_store: SqliteAgentStore
    ) -> None:
        agent_store.ensure_default()
        reply = await gateway.handle_inbound_message(_cli_message("hello"))
        assert "prompt:" in reply.text

    @pytest.mark.asyncio
    async def test_profile_injected_with_custom_agent(
        self, gateway: RuntimeGateway, agent_store: SqliteAgentStore
    ) -> None:
        agent_store.save(AgentConfig(
            id="ag_custom", name="Custom",
            source_expert="general-assistant", system_prompt="You are a pirate.",
        ))
        session = await gateway.create_session_for_agent(_WEB_PK, "ag_custom", **_WEB_ID)
        assert session.agent_id == "ag_custom"

        reply = await gateway.handle_inbound_message(
            _web_message("ahoy", session_id=session.session_id)
        )
        assert "pirate" in reply.text

    @pytest.mark.asyncio
    async def test_no_resolver_backward_compat(
        self, gateway_no_resolver: RuntimeGateway
    ) -> None:
        reply = await gateway_no_resolver.handle_inbound_message(_cli_message("hello"))
        assert "no profile" in reply.text


class TestResolveSession:
    @pytest.mark.asyncio
    async def test_web_with_session_id(
        self, gateway: RuntimeGateway
    ) -> None:
        session = await gateway.create_new_session(_WEB_PK, **_WEB_ID)
        reply = await gateway.handle_inbound_message(
            _web_message("hello", session_id=session.session_id)
        )
        assert "echo:" in reply.text

    @pytest.mark.asyncio
    async def test_web_with_invalid_session_id(self, gateway: RuntimeGateway) -> None:
        with pytest.raises(SessionNotFoundError):
            await gateway.handle_inbound_message(
                _web_message("hello", session_id="nonexistent")
            )

    @pytest.mark.asyncio
    async def test_cli_uses_active_session(
        self, gateway_no_resolver: RuntimeGateway
    ) -> None:
        reply = await gateway_no_resolver.handle_inbound_message(_cli_message("hello"))
        assert "echo:" in reply.text


class TestCreateSessionForAgent:
    @pytest.mark.asyncio
    async def test_creates_with_agent_id(
        self, gateway: RuntimeGateway, agent_store: SqliteAgentStore
    ) -> None:
        agent_store.save(AgentConfig(
            id="ag_code", name="Code",
            source_expert="code-helper", system_prompt="Code expert",
        ))
        session = await gateway.create_session_for_agent(_WEB_PK, "ag_code", **_WEB_ID)
        assert session.agent_id == "ag_code"
        assert session.channel == "web"

    @pytest.mark.asyncio
    async def test_creates_and_sets_active(
        self, gateway: RuntimeGateway, session_store: InMemorySessionStore
    ) -> None:
        session = await gateway.create_session_for_agent(
            _WEB_PK, "default-agent", **_WEB_ID
        )
        active = await session_store.get_active("web:default:web")
        assert active is not None
        assert active.session_id == session.session_id


class TestGetSessionById:
    @pytest.mark.asyncio
    async def test_returns_session(
        self, gateway: RuntimeGateway
    ) -> None:
        session = await gateway.create_new_session(_CLI_PK, **_CLI_ID)
        found = await gateway.get_session_by_id(session.session_id)
        assert found is not None
        assert found.session_id == session.session_id

    @pytest.mark.asyncio
    async def test_returns_none_for_nonexistent(self, gateway: RuntimeGateway) -> None:
        found = await gateway.get_session_by_id("nonexistent")
        assert found is None


class TestStreamWithAgentProfile:
    @pytest.mark.asyncio
    async def test_stream_uses_agent_prompt(
        self, gateway: RuntimeGateway, agent_store: SqliteAgentStore
    ) -> None:
        agent_store.save(AgentConfig(
            id="ag_stream", name="Stream",
            source_expert="general-assistant", system_prompt="Stream expert",
        ))
        session = await gateway.create_session_for_agent(_WEB_PK, "ag_stream", **_WEB_ID)
        chunks = []
        async for chunk in gateway.handle_stream(
            _web_message("hello", session_id=session.session_id)
        ):
            chunks.append(chunk)
        full = "".join(c.text for c in chunks if c.type == "content")
        assert "Stream expert" in full
