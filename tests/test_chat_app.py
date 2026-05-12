"""CLI 冒烟测试：验证 chat.app 的基本交互和退出行为。"""

from __future__ import annotations

import asyncio

from chat.app import run
from claw.agent import MiniClaw
from claw.channels.local import LocalDelivery
from claw.runner import EchoAgentRunner
from claw.types import StreamChunk


def _echo_claw() -> MiniClaw:
    return MiniClaw(delivery=LocalDelivery(), agent_runner=EchoAgentRunner())


def test_chat_app_exits_on_exit_command(monkeypatch, capsys) -> None:
    """输入 /exit 时应正常退出，并打印欢迎信息。"""
    monkeypatch.setattr("builtins.input", lambda _: "/exit")
    asyncio.run(run(_echo_claw()))
    captured = capsys.readouterr()
    assert "Mini Claw chat" in captured.out


def test_chat_app_prints_agent_reply_for_user_message(monkeypatch, capsys) -> None:
    """输入一条消息后再退出，应打印 Agent 的 echo 回复。"""
    inputs = iter(["hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    asyncio.run(run(_echo_claw()))
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

    claw = MiniClaw(delivery=LocalDelivery(), agent_runner=_ThinkingRunner())
    inputs = iter(["hi", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    asyncio.run(run(claw))
    captured = capsys.readouterr()
    # thinking 连续输出，只有一次 [think] 前缀
    assert captured.out.count("[think]") == 1
    assert "hmm" in captured.out
    assert "answer" in captured.out
