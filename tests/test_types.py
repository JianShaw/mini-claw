"""领域类型测试：验证 dataclass 默认值隔离和必填字段。"""

from __future__ import annotations

from claw.types import AgentReply, InboundMessage, PlatformEvent, Session


def test_inbound_message_defaults_metadata_to_empty_dict() -> None:
    """InboundMessage 的 metadata 默认应为空 dict。"""
    msg = InboundMessage(
        channel="local",
        account_id="app",
        peer_id="user",
        sender_id="user",
        message_id="1",
        text="hi",
        timestamp=0,
        message_type="text",
        raw=None,
    )
    assert msg.metadata == {}


def test_session_defaults_history_to_empty_list() -> None:
    """Session 的 history 默认应为空 list。"""
    session = Session(
        session_id="s1",
        session_key="local:app:user",
        channel="local",
        account_id="app",
        peer_id="user",
        sender_id="user",
        agent_id="default",
    )
    assert session.history == []


def test_agent_reply_defaults_metadata_to_empty_dict() -> None:
    """AgentReply 的 metadata 默认应为空 dict。"""
    reply = AgentReply(text="hello")
    assert reply.metadata == {}


def test_defaults_are_not_shared_between_instances() -> None:
    """两个实例的默认容器必须互相独立，修改一个不影响另一个。"""
    a = InboundMessage(
        channel="local",
        account_id="app",
        peer_id="u1",
        sender_id="u1",
        message_id="1",
        text="hi",
        timestamp=0,
        message_type="text",
        raw=None,
    )
    b = InboundMessage(
        channel="local",
        account_id="app",
        peer_id="u2",
        sender_id="u2",
        message_id="2",
        text="ho",
        timestamp=0,
        message_type="text",
        raw=None,
    )
    a.metadata["foo"] = "bar"
    assert "foo" not in b.metadata
