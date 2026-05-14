"""MiniClaw 门面测试：验证对外接口兼容性和运行时集成。"""

from __future__ import annotations

from claw.agent import MiniClaw
from claw.channels.local import LocalDelivery
from claw.runner import EchoAgentRunner
from claw.session import InMemorySessionStore
from claw.tools import Tool, ToolsRegistry
from claw.types import StreamChunk


def _make_claw(delivery: LocalDelivery) -> MiniClaw:
    return MiniClaw(
        delivery=delivery,
        agent_runner=EchoAgentRunner(),
        session_store=InMemorySessionStore(),
    )


def test_mini_claw_reply_keeps_existing_echo_behavior() -> None:
    """同步接口 reply() 应保持原有 echo 行为不变。"""
    delivery = LocalDelivery()
    claw = _make_claw(delivery)
    assert claw.reply("hello").text == "echo: hello"


async def test_mini_claw_areply_routes_through_delivery() -> None:
    """异步接口 areply() 应将回复通过 Delivery 投递。"""
    delivery = LocalDelivery()
    claw = _make_claw(delivery)
    await claw.areply("hello")
    assert len(delivery.sent) == 1
    assert delivery.sent[0][1].text == "echo: hello"


async def test_mini_claw_preserves_history_across_messages() -> None:
    """多轮对话应共享同一个 Session，history 累积。"""
    delivery = LocalDelivery()
    claw = _make_claw(delivery)
    await claw.areply("first")
    await claw.areply("second")
    session = await claw.gateway._session_store.get_active("local:local-app:local-user")
    assert session is not None
    assert len(session.history) == 4


# --- areply_stream 测试 ---


async def test_mini_claw_areply_stream_yields_text() -> None:
    """areply_stream 应逐步产出 StreamChunk。"""
    delivery = LocalDelivery()
    claw = _make_claw(delivery)
    chunks: list[StreamChunk] = []
    async for chunk in claw.areply_stream("hello"):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].type == "content"
    assert chunks[0].text == "echo: hello"


async def test_mini_claw_areply_stream_saves_history() -> None:
    """areply_stream 流结束后 history 应正确保存。"""
    delivery = LocalDelivery()
    claw = _make_claw(delivery)
    async for _ in claw.areply_stream("hello"):
        pass
    session = await claw.gateway._session_store.get_active("local:local-app:local-user")
    assert session is not None
    assert len(session.history) == 2


async def test_mini_claw_areply_stream_routes_through_delivery() -> None:
    """areply_stream 流结束后 Delivery 应被调用。"""
    delivery = LocalDelivery()
    claw = _make_claw(delivery)
    async for _ in claw.areply_stream("hello"):
        pass
    assert len(delivery.sent) == 1
    assert delivery.sent[0][1].text == "echo: hello"


# --- 会话管理便捷方法测试 ---


async def test_mini_claw_new_session() -> None:
    """new_session 应创建新会话并激活。"""
    claw = _make_claw(LocalDelivery())
    await claw.areply("first")
    first_id = await claw.get_active_session_id()

    new = await claw.new_session()
    assert new.session_id != first_id
    assert await claw.get_active_session_id() == new.session_id


async def test_mini_claw_list_sessions() -> None:
    """list_sessions 应列出所有会话。"""
    claw = _make_claw(LocalDelivery())
    await claw.areply("first")
    await claw.new_session()

    sessions = await claw.list_sessions()
    assert len(sessions) == 2


async def test_mini_claw_select_session() -> None:
    """select_session 应切换活跃会话。"""
    claw = _make_claw(LocalDelivery())
    await claw.areply("first")
    first_id = await claw.get_active_session_id()
    new = await claw.new_session()

    result = await claw.select_session(first_id)
    assert result is not None
    assert await claw.get_active_session_id() == first_id


async def test_mini_claw_delete_session() -> None:
    """delete_session 应删除指定会话。"""
    claw = _make_claw(LocalDelivery())
    await claw.areply("first")
    first_id = await claw.get_active_session_id()
    new = await claw.new_session()

    await claw.delete_session(first_id)
    sessions = await claw.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id == new.session_id


# --- ToolsRegistry 注入测试 ---


def test_mini_claw_without_tools_registry_unchanged() -> None:
    """不传 tools_registry 时行为与原来一致。"""
    delivery = LocalDelivery()
    claw = MiniClaw(
        delivery=delivery,
        agent_runner=EchoAgentRunner(),
        session_store=InMemorySessionStore(),
    )
    assert claw.reply("hello").text == "echo: hello"


async def test_mini_claw_with_tools_registry() -> None:
    """传入 tools_registry 时应传递给 agent runner（通过 DeepSeekAgentRunner 接收）。"""
    registry = ToolsRegistry()

    async def _handler(args: dict) -> str:
        return "ok"

    registry.register(Tool(name="test_tool", description="test", handler=_handler))

    delivery = LocalDelivery()
    claw = MiniClaw(
        delivery=delivery,
        agent_runner=EchoAgentRunner(),
        session_store=InMemorySessionStore(),
        tools_registry=registry,
    )
    # 使用 areply 而不是 reply，因为 reply() 内部调用 asyncio.run()，在 async 测试中会冲突
    reply = await claw.areply("hello")
    assert reply.text == "echo: hello"


# --- MCP 集成测试 ---


async def test_mini_claw_start_stop_without_mcp() -> None:
    """不传 mcp_config_path 时 start/stop 应为空操作。"""
    delivery = LocalDelivery()
    claw = MiniClaw(
        delivery=delivery,
        agent_runner=EchoAgentRunner(),
        session_store=InMemorySessionStore(),
    )
    await claw.start()
    assert claw.get_mcp_status() == []
    await claw.stop()


async def test_mini_claw_start_with_nonexistent_config() -> None:
    """mcp_config_path 指向不存在的文件时应静默跳过。"""
    delivery = LocalDelivery()
    claw = MiniClaw(
        delivery=delivery,
        agent_runner=EchoAgentRunner(),
        session_store=InMemorySessionStore(),
        mcp_config_path="/nonexistent/mcp_config.json",
    )
    await claw.start()
    assert claw.get_mcp_status() == []
    await claw.stop()
