"""MiniClaw 门面测试：验证对外接口兼容性和运行时集成。"""

from __future__ import annotations

from claw.agent import MiniClaw
from claw.channels.local import LocalDelivery
from claw.runner import EchoAgentRunner


def _make_claw(delivery: LocalDelivery) -> MiniClaw:
    return MiniClaw(delivery=delivery, agent_runner=EchoAgentRunner())


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
    session = await claw.gateway._session_store.get("local:local-app:local-user")
    assert session is not None
    assert len(session.history) == 4
