"""Tests for Web tool discovery API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claw.agent_runtime.resolver import AgentResolver
from claw.agent_runtime.store import SqliteAgentStore
from claw.expert.store import SqliteExpertStore
from claw.gateway import RuntimeGateway
from claw.session import InMemorySessionStore
from claw.storage.sqlite import get_connection, init_db
from claw.types import AgentReply, StreamChunk
from web.backend.app import create_app


class EchoRunner:
    async def run(self, session, message):
        return AgentReply(text=f"echo: {message.text}")

    async def run_stream(self, session, message):
        yield StreamChunk(type="content", text=f"echo: {message.text}")


class SilentDelivery:
    async def send(self, message, reply):
        pass


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    conn = get_connection(tmp_path / "test.sqlite")
    init_db(conn)
    expert_store = SqliteExpertStore(conn)
    agent_store = SqliteAgentStore(conn)
    expert_store.init_bundled()
    agent_store.ensure_default()

    gateway = RuntimeGateway(
        session_store=InMemorySessionStore(),
        agent_runner=EchoRunner(),
        delivery=SilentDelivery(),
        agent_resolver=AgentResolver(agent_store),
    )
    app = create_app(
        gateway=gateway,
        expert_store=expert_store,
        agent_store=agent_store,
    )
    return TestClient(app)


def test_list_tools(client: TestClient):
    resp = client.get("/api/v1/tools")
    assert resp.status_code == 200
    data = resp.json()
    names = {tool["name"] for tool in data}
    assert {"calculator", "current_time", "file_search", "load_skill"}.issubset(names)


def test_tool_schema_contains_description_and_parameters(client: TestClient):
    resp = client.get("/api/v1/tools")
    assert resp.status_code == 200
    calculator = next(t for t in resp.json() if t["name"] == "calculator")
    assert calculator["description"]
    assert calculator["parameters"]["type"] == "object"
