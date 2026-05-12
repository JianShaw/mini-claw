"""CLI 冒烟测试：验证 chat.app 的基本交互和退出行为。"""

from __future__ import annotations

from chat.app import run


def test_chat_app_exits_on_exit_command(monkeypatch, capsys) -> None:
    """输入 /exit 时应正常退出，并打印欢迎信息。"""
    monkeypatch.setattr("builtins.input", lambda _: "/exit")
    run()
    captured = capsys.readouterr()
    assert "Mini Claw chat" in captured.out


def test_chat_app_prints_agent_reply_for_user_message(monkeypatch, capsys) -> None:
    """输入一条消息后再退出，应打印 Agent 的 echo 回复。"""
    inputs = iter(["hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    run()
    captured = capsys.readouterr()
    assert "echo: hello" in captured.out
