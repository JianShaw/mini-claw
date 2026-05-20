"""测试 Web API：Expert + Agent + Conversation + Chat 端点。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from claw.agent_runtime.resolver import AgentResolver
from claw.agent_runtime.store import SqliteAgentStore
from claw.expert.store import SqliteExpertStore
from claw.gateway import RuntimeGateway
from claw.session import InMemorySessionStore
from claw.storage.sqlite import get_connection, init_db
from claw.types import AgentReply, InboundMessage, Session, StreamChunk
from web.backend.app import create_app


# ---- Test doubles ----

class EchoRunner:
    async def run(self, session, message):
        return AgentReply(text=f"echo: {message.text}")

    async def run_stream(self, session, message):
        yield StreamChunk(type="content", text=f"echo: {message.text}")


class SilentDelivery:
    async def send(self, message, reply):
        pass


@pytest.fixture
def conn(tmp_path: Path) -> None:
    c = get_connection(tmp_path / "test.sqlite")
    init_db(c)
    return c


@pytest.fixture
def app(conn):
    expert_store = SqliteExpertStore(conn)
    agent_store = SqliteAgentStore(conn)
    expert_store.init_bundled()
    agent_store.ensure_default()

    resolver = AgentResolver(agent_store)
    gateway = RuntimeGateway(
        session_store=InMemorySessionStore(),
        agent_runner=EchoRunner(),
        delivery=SilentDelivery(),
        agent_resolver=resolver,
    )

    app = create_app(
        gateway=gateway,
        expert_store=expert_store,
        agent_store=agent_store,
    )
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


# ---- Expert API ----

class TestExpertAPI:
    def test_list_experts(self, client):
        resp = client.get("/api/v1/experts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        names = [e["name"] for e in data]
        assert "general-assistant" in names

    def test_get_expert(self, client):
        resp = client.get("/api/v1/experts/general-assistant")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "general-assistant"
        assert data["display_name"] == "General Assistant"
        assert data["system_prompt"]

    def test_get_expert_not_found(self, client):
        resp = client.get("/api/v1/experts/nonexistent")
        assert resp.status_code == 404

    def test_install_bundled(self, client):
        resp = client.post("/api/v1/experts/code-helper/install")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "code-helper"
        assert data["source"] == "bundled"

    def test_delete_bundled_forbidden(self, client):
        resp = client.delete("/api/v1/experts/general-assistant")
        assert resp.status_code == 400

    def test_search_experts(self, client):
        resp = client.get("/api/v1/experts", params={"q": "code"})
        assert resp.status_code == 200
        data = resp.json()
        # 至少有 code-helper 或 description 中含 code 的
        assert isinstance(data, list)


# ---- Agent API ----

class TestAgentAPI:
    def test_create_agent(self, client):
        # 先安装 expert
        client.post("/api/v1/experts/code-helper/install")
        resp = client.post("/api/v1/agents", json={
            "expert_name": "code-helper",
            "agent_name": "My Code Helper",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"].startswith("ag_")
        assert data["name"] == "My Code Helper"
        assert data["source_expert"] == "code-helper"

    def test_list_agents(self, client):
        resp = client.get("/api/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        # 至少有 default-agent
        ids = [a["id"] for a in data]
        assert "default-agent" in ids

    def test_get_agent(self, client):
        resp = client.get("/api/v1/agents/default-agent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "default-agent"

    def test_get_agent_not_found(self, client):
        resp = client.get("/api/v1/agents/nonexistent")
        assert resp.status_code == 404

    def test_update_agent(self, client):
        resp = client.put("/api/v1/agents/default-agent", json={
            "name": "Updated Agent",
            "system_prompt": "New prompt",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Updated Agent"
        assert data["system_prompt"] == "New prompt"

    def test_delete_agent(self, client):
        client.post("/api/v1/experts/code-helper/install")
        create_resp = client.post("/api/v1/agents", json={"expert_name": "code-helper"})
        agent_id = create_resp.json()["id"]

        del_resp = client.delete(f"/api/v1/agents/{agent_id}")
        assert del_resp.status_code == 204

        get_resp = client.get(f"/api/v1/agents/{agent_id}")
        assert get_resp.status_code == 404


# ---- Conversation API ----

class TestConversationAPI:
    def test_create_conversation(self, client):
        resp = client.post("/api/v1/conversations", json={
            "agent_id": "default-agent",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["session_id"]
        assert data["agent_id"] == "default-agent"

    def test_list_conversations(self, client):
        client.post("/api/v1/conversations", json={"agent_id": "default-agent"})
        resp = client.get("/api/v1/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

    def test_get_conversation(self, client):
        create_resp = client.post("/api/v1/conversations", json={"agent_id": "default-agent"})
        sid = create_resp.json()["session_id"]
        resp = client.get(f"/api/v1/conversations/{sid}")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == sid

    def test_delete_conversation(self, client):
        create_resp = client.post("/api/v1/conversations", json={"agent_id": "default-agent"})
        sid = create_resp.json()["session_id"]
        del_resp = client.delete(f"/api/v1/conversations/{sid}")
        assert del_resp.status_code == 204


# ---- Chat SSE API ----

class TestChatAPI:
    def test_chat_stream(self, client):
        # 创建对话
        create_resp = client.post("/api/v1/conversations", json={"agent_id": "default-agent"})
        sid = create_resp.json()["session_id"]

        # 发送聊天（SSE）
        resp = client.post("/api/v1/chat/stream", json={
            "session_id": sid,
            "text": "hello",
        })
        assert resp.status_code == 200
        # 验证 SSE 格式
        content = resp.text
        assert "echo:" in content
        assert "[DONE]" in content
