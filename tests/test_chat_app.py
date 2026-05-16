"""CLI 冒烟测试：验证 chat.app 的基本交互、退出行为和会话管理命令。"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from chat.app import _print_scheduled_deliveries, run
from claw.agent import MiniClaw
from claw.channels.local import LocalDelivery
from claw.runner import EchoAgentRunner
from claw.session import InMemorySessionStore
from claw.types import AgentReply, InboundMessage, StreamChunk


def _echo_claw() -> MiniClaw:
    return MiniClaw(
        delivery=LocalDelivery(),
        agent_runner=EchoAgentRunner(),
        session_store=InMemorySessionStore(),
    )


def test_chat_app_exits_on_exit_command(monkeypatch, capsys) -> None:
    """输入 /exit 时应正常退出，并打印欢迎信息。"""
    monkeypatch.setattr("builtins.input", lambda _: "/exit")
    claw = _echo_claw()
    # start/stop 在 run() 内部调用，不需要手动调用
    asyncio.run(run(claw))
    captured = capsys.readouterr()
    assert "Mini Claw chat" in captured.out


def test_chat_app_prints_agent_reply_for_user_message(monkeypatch, capsys) -> None:
    """输入一条消息后再退出，应打印 Agent 的 echo 回复。"""
    inputs = iter(["hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    claw = _echo_claw()
    asyncio.run(run(claw))
    captured = capsys.readouterr()
    assert "echo: hello" in captured.out


def test_chat_app_prints_thinking_with_prefix(monkeypatch, capsys) -> None:
    """thinking 内容应作为连续块显示，只在开头打印一次 [think] 前缀。"""

    class _ThinkingRunner:
        async def run(self, session, message):
            from claw.types import ChatMessage as CM, AgentReply as AR
            session.history.append(CM(role="user", content=message.text))
            session.history.append(CM(role="assistant", content="answer"))
            return AR(text="answer")

        async def run_stream(self, session, message):
            from claw.types import ChatMessage as CM
            session.history.append(CM(role="user", content=message.text))
            yield StreamChunk(type="thinking", text="hmm")
            yield StreamChunk(type="thinking", text=" let me think")
            yield StreamChunk(type="content", text="answer")

    claw = MiniClaw(
        delivery=LocalDelivery(),
        agent_runner=_ThinkingRunner(),
        session_store=InMemorySessionStore(),
    )
    # 不传 mcp_config_path，避免 start() 尝试连接
    inputs = iter(["hi", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    asyncio.run(run(claw))
    captured = capsys.readouterr()
    assert captured.out.count("[think]") == 1
    assert "hmm" in captured.out
    assert "answer" in captured.out


async def test_chat_app_prints_scheduled_delivery_from_queue(capsys) -> None:
    """后台 scheduler 投递到 LocalDelivery 后，CLI 应主动打印出来。"""
    delivery = LocalDelivery()
    task = asyncio.create_task(_print_scheduled_deliveries(delivery, asyncio.Lock()))
    try:
        normal_msg = InboundMessage(
            channel="local",
            account_id="app",
            peer_id="user",
            sender_id="user",
            message_id="normal",
            text="normal",
            timestamp=0,
            message_type="text",
            raw=None,
        )
        scheduled_msg = InboundMessage(
            channel="local",
            account_id="app",
            peer_id="user",
            sender_id="scheduler",
            message_id="scheduled",
            text="remind",
            timestamp=0,
            message_type="text",
            raw=None,
            metadata={"scheduled": True, "task_name": "sleep"},
        )
        await delivery.send(normal_msg, AgentReply(text="normal reply"))
        await delivery.send(scheduled_msg, AgentReply(text="sleep now"))
        await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    captured = capsys.readouterr()
    assert "claw[sleep]>" in captured.out
    assert "sleep now" in captured.out
    assert "normal reply" not in captured.out


# --- 会话管理命令测试 ---


def test_chat_app_new_command(monkeypatch, capsys) -> None:
    """/new 应创建新会话并打印 session_id。"""
    inputs = iter(["/new", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    asyncio.run(run(_echo_claw()))
    captured = capsys.readouterr()
    assert "New session: sess_" in captured.out


def test_chat_app_sessions_command(monkeypatch, capsys) -> None:
    """/sessions 应列出会话。"""
    inputs = iter(["hello", "/sessions", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    asyncio.run(run(_echo_claw()))
    captured = capsys.readouterr()
    assert "sess_" in captured.out


def test_chat_app_help_command(monkeypatch, capsys) -> None:
    """/help 应显示帮助信息。"""
    inputs = iter(["/help", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    asyncio.run(run(_echo_claw()))
    captured = capsys.readouterr()
    assert "/new" in captured.out
    assert "/compact" in captured.out


def test_chat_app_select_command(monkeypatch, capsys) -> None:
    """/select 应切换到指定会话。"""
    inputs = iter(["hello", "/new", "/sessions", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    asyncio.run(run(_echo_claw()))
    captured = capsys.readouterr()
    # /new 创建了第二个 session
    assert "New session:" in captured.out


def test_chat_app_compact_command(monkeypatch, capsys) -> None:
    """/compact 应压缩当前会话上下文。"""
    inputs = iter(["hello", "/compact", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    asyncio.run(run(_echo_claw()))
    captured = capsys.readouterr()
    assert "Compacted." in captured.out


def test_chat_app_compact_empty_session(monkeypatch, capsys) -> None:
    """/compact 在空会话时应提示无内容。"""
    inputs = iter(["/compact", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    asyncio.run(run(_echo_claw()))
    captured = capsys.readouterr()
    assert "No active session" in captured.out or "empty" in captured.out.lower()


# --- MCP 命令测试 ---


def test_chat_app_mcp_command_not_configured(monkeypatch, capsys) -> None:
    """/mcp 在无 MCP 配置时应提示未配置。"""
    claw = MiniClaw(
        delivery=LocalDelivery(),
        agent_runner=EchoAgentRunner(),
        session_store=InMemorySessionStore(),
    )
    inputs = iter(["/mcp", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    asyncio.run(run(claw))
    captured = capsys.readouterr()
    assert "MCP not configured" in captured.out


def test_chat_app_help_shows_mcp_command(monkeypatch, capsys) -> None:
    """/help 应包含 /mcp 命令说明。"""
    inputs = iter(["/help", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    asyncio.run(run(_echo_claw()))
    captured = capsys.readouterr()
    assert "/mcp" in captured.out
