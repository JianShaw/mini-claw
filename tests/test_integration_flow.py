"""V1 集成测试：Expert → Agent → Session → Chat 全链路验证。"""

from __future__ import annotations

from pathlib import Path
from collections.abc import AsyncIterator

import pytest

from claw.agent_runtime.factory import AgentFactory
from claw.agent_runtime.resolver import AgentResolver
from claw.agent_runtime.store import SqliteAgentStore
from claw.expert.service import ExpertService
from claw.expert.store import SqliteExpertStore
from claw.gateway import RuntimeGateway
from claw.session import InMemorySessionStore
from claw.storage.sqlite import get_connection, init_db
from claw.types import AgentReply, InboundMessage, Session, StreamChunk


class EchoRunner:
    async def run(self, session, message):
        profile = session.metadata.get("agent_runtime_profile")
        prompt = profile["system_prompt"] if profile else ""
        model = profile["model_config"].get("name", "") if profile else ""
        return AgentReply(text=f"[{model}] {prompt[:30]}: {message.text}")

    async def run_stream(self, session, message):
        profile = session.metadata.get("agent_runtime_profile")
        prompt = profile["system_prompt"] if profile else ""
        yield StreamChunk(type="content", text=f"echo: {message.text} | {prompt[:20]}")


class SilentDelivery:
    async def send(self, message, reply):
        pass


def _web_msg(text: str, session_id: str | None = None) -> InboundMessage:
    return InboundMessage(
        channel="web", account_id="default", peer_id="web", sender_id="web",
        message_id="msg", text=text, timestamp=0, message_type="text", raw=None,
        metadata={"session_id": session_id} if session_id else {},
    )


@pytest.fixture
def conn(tmp_path: Path):
    c = get_connection(tmp_path / "test.sqlite")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def expert_store(conn):
    return SqliteExpertStore(conn)


@pytest.fixture
def agent_store(conn):
    return SqliteAgentStore(conn)


@pytest.fixture
def gateway(expert_store, agent_store):
    resolver = AgentResolver(agent_store)
    return RuntimeGateway(
        session_store=InMemorySessionStore(),
        agent_runner=EchoRunner(),
        delivery=SilentDelivery(),
        agent_resolver=resolver,
    )


class TestExpertAgentSessionFlow:
    """全链路集成测试：覆盖方案验证场景 1-7 + 11。"""

    @pytest.mark.asyncio
    async def test_full_flow(self, expert_store, agent_store, gateway):
        # 1. 安装 Expert → 验证存在
        expert_store.init_bundled()
        expert = expert_store.get("code-helper")
        assert expert is not None
        assert expert.source == "bundled"

        # 2. 从 Expert 创建 Agent → 验证字段复制
        factory = AgentFactory(expert_store, agent_store)
        agent = factory.create_from_expert("code-helper", agent_name="My Code Helper")
        assert agent.source_expert == "code-helper"
        assert agent.system_prompt == expert.system_prompt
        assert "read_file" in agent.enabled_tools
        assert "code-review" in agent.enabled_skills

        # 3. 创建 Session(agent_id) → 验证绑定
        session = await gateway.create_session_for_agent(
            _web_msg("setup"), agent.id
        )
        assert session.agent_id == agent.id

        # 4. 发消息 → 验证 RuntimeProfile 注入
        reply = await gateway.handle_inbound_message(
            _web_msg("hello", session_id=session.session_id)
        )
        assert "echo" in reply.text or "programming" in reply.text

        # 5. 修改 Agent system_prompt → 验证下次消息使用新 prompt
        agent.system_prompt = "You are a pirate!"
        agent_store.save(agent)
        reply2 = await gateway.handle_inbound_message(
            _web_msg("ahoy", session_id=session.session_id)
        )
        assert "pirate" in reply2.text

        # 6. 创建另一个 Agent → 不同 prompt
        agent2 = factory.create_from_expert("general-assistant")
        session2 = await gateway.create_session_for_agent(
            _web_msg("setup2"), agent2.id
        )
        reply3 = await gateway.handle_inbound_message(
            _web_msg("hello", session_id=session2.session_id)
        )
        assert "pirate" not in reply3.text  # 不同 Agent，不同 prompt

        # 7. 回归：CLI 默认 agent 可用
        agent_store.ensure_default()
        reply_cli = await gateway.handle_inbound_message(
            InboundMessage(
                channel="local", account_id="default", peer_id="cli",
                sender_id="cli", message_id="m1", text="cli hello",
                timestamp=0, message_type="text", raw=None,
            )
        )
        assert "Mini Claw" in reply_cli.text or "echo" in reply_cli.text

    @pytest.mark.asyncio
    async def test_stream_flow(self, expert_store, agent_store, gateway):
        """流式聊天全链路。"""
        expert_store.init_bundled()
        agent_store.ensure_default()

        session = await gateway.create_session_for_agent(
            _web_msg("setup"), "default-agent"
        )
        chunks = []
        async for chunk in gateway.handle_stream(
            _web_msg("hello stream", session_id=session.session_id)
        ):
            chunks.append(chunk)

        content = "".join(c.text for c in chunks if c.type == "content")
        assert "echo" in content

    @pytest.mark.asyncio
    async def test_expert_marketplace_web_flow(self, expert_store, agent_store, gateway):
        """模拟 Web 端：浏览专家 → 安装 → 创建 Agent → 创建对话 → 聊天。"""
        service = ExpertService(expert_store)

        # 安装 bundled
        expert = service.install_bundled("code-helper")
        assert expert.name == "code-helper"

        # 列出专家
        experts = service.list_experts()
        assert any(e.name == "code-helper" for e in experts)

        # 创建 Agent
        factory = AgentFactory(expert_store, agent_store)
        agent = factory.create_from_expert("code-helper")
        assert agent.source_expert == "code-helper"

        # 创建对话
        session = await gateway.create_session_for_agent(
            _web_msg("setup"), agent.id
        )
        assert session.agent_id == agent.id

        # 发消息
        reply = await gateway.handle_inbound_message(
            _web_msg("review this code", session_id=session.session_id)
        )
        assert reply.text  # 非空
