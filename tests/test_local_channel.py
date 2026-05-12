"""本地通道测试：LocalTransport、LocalAdapter、LocalDelivery、JsonlDelivery。"""

from __future__ import annotations

import json
from pathlib import Path

from claw.channels.local import (
    JsonlDelivery,
    LocalAdapter,
    LocalDelivery,
    LocalTransport,
)
from claw.types import AgentReply, InboundMessage, PlatformEvent


def _event(**overrides: object) -> PlatformEvent:
    """构造 PlatformEvent 的辅助函数。"""
    defaults: dict[str, object] = dict(
        platform="local",
        transport="cli",
        event_id="e1",
        received_at=0,
        payload={
            "peer_id": "user",
            "sender_id": "user",
            "message_id": "m1",
            "text": "hello",
        },
    )
    defaults.update(overrides)
    return PlatformEvent(**defaults)  # type: ignore[arg-type]


# --- LocalTransport ---


def test_local_transport_receives_text_and_produces_platform_event() -> None:
    """LocalTransport.receive() 应将文本转为包含完整字段的 PlatformEvent。"""
    transport = LocalTransport()
    event = transport.receive("hello")
    assert event.platform == "local"
    assert event.transport == "cli"
    assert event.event_id.startswith("local-")
    assert event.payload["text"] == "hello"
    assert event.payload["peer_id"] == "local-user"
    assert event.payload["sender_id"] == "local-user"
    assert event.payload["account_id"] == "local-app"


def test_local_transport_generates_unique_event_ids() -> None:
    """每次 receive() 应生成不同的 event_id。"""
    transport = LocalTransport()
    e1 = transport.receive("a")
    e2 = transport.receive("b")
    assert e1.event_id != e2.event_id


def test_local_transport_uses_custom_ids() -> None:
    """构造时可自定义 platform、transport、account_id、peer_id、sender_id。"""
    transport = LocalTransport(
        platform="test",
        transport="api",
        account_id="bot-1",
        peer_id="user-42",
        sender_id="user-42",
    )
    event = transport.receive("hi")
    assert event.platform == "test"
    assert event.transport == "api"
    assert event.payload["account_id"] == "bot-1"
    assert event.payload["peer_id"] == "user-42"


# --- LocalAdapter ---


def test_local_adapter_maps_payload_to_inbound_message() -> None:
    """LocalAdapter 应正确将 payload 字段映射到 InboundMessage。"""
    adapter = LocalAdapter()
    msg = adapter.to_inbound_message(_event())
    assert msg.channel == "local"
    assert msg.peer_id == "user"
    assert msg.text == "hello"
    assert msg.message_type == "text"


def test_local_adapter_uses_local_defaults() -> None:
    """payload 缺少 account_id 和 text 时，应使用 local 默认值。"""
    adapter = LocalAdapter()
    msg = adapter.to_inbound_message(
        _event(payload={"peer_id": "user", "sender_id": "user", "message_id": "m1"})
    )
    assert msg.account_id == "local-app"
    assert msg.text == ""


# --- LocalDelivery ---


async def test_local_delivery_records_sent_reply() -> None:
    """LocalDelivery.send() 应将 (message, reply) 追加到 sent 列表。"""
    delivery = LocalDelivery()
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
    reply = AgentReply(text="ok")
    await delivery.send(msg, reply)
    assert len(delivery.sent) == 1
    assert delivery.sent[0] == (msg, reply)


# --- JsonlDelivery ---


async def test_jsonl_delivery_writes_records_to_file(tmp_path: Path) -> None:
    """JsonlDelivery 应将 user 和 assistant 记录写入 JSONL 文件。"""
    delivery = JsonlDelivery(data_dir=tmp_path / "chat")
    msg = InboundMessage(
        channel="local",
        account_id="app",
        peer_id="user",
        sender_id="user",
        message_id="m1",
        text="hello",
        timestamp=1000,
        message_type="text",
        raw=None,
        metadata={"session_id": "sess_abc"},
    )
    reply = AgentReply(text="echo: hello")
    await delivery.send(msg, reply)

    # 验证文件已创建
    filepath = tmp_path / "chat" / "local_app_user.jsonl"
    assert filepath.exists()

    # 验证内容
    lines = filepath.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    user_record = json.loads(lines[0])
    assert user_record["role"] == "user"
    assert user_record["text"] == "hello"
    assert user_record["message_id"] == "m1"
    assert user_record["session_id"] == "sess_abc"

    assistant_record = json.loads(lines[1])
    assert assistant_record["role"] == "assistant"
    assert assistant_record["text"] == "echo: hello"


async def test_jsonl_delivery_appends_to_existing_file(tmp_path: Path) -> None:
    """多次 send 应追加到同一文件，不覆盖之前的内容。"""
    delivery = JsonlDelivery(data_dir=tmp_path)
    msg1 = InboundMessage(
        channel="local",
        account_id="app",
        peer_id="user",
        sender_id="user",
        message_id="m1",
        text="first",
        timestamp=1000,
        message_type="text",
        raw=None,
    )
    msg2 = InboundMessage(
        channel="local",
        account_id="app",
        peer_id="user",
        sender_id="user",
        message_id="m2",
        text="second",
        timestamp=2000,
        message_type="text",
        raw=None,
    )
    await delivery.send(msg1, AgentReply(text="echo: first"))
    await delivery.send(msg2, AgentReply(text="echo: second"))

    filepath = tmp_path / "local_app_user.jsonl"
    lines = filepath.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 4  # 2 sends x 2 records each
