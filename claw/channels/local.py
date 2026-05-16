"""本地通道：CLI 和测试专用的无网络通道实现。

LocalTransport —— 将 CLI 文本输入包装成 PlatformEvent
LocalAdapter   —— 把 payload 转为标准 InboundMessage
LocalDelivery  —— 不真正发送，只记录到 sent 列表（测试用）
JsonlDelivery  —— 将聊天记录按会话持久化到 JSONL 文件
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from time import time
from typing import Any

from claw.types import AgentReply, InboundMessage, PlatformEvent


class LocalTransport:
    """本地传输层：将 CLI 文本输入包装成 PlatformEvent。"""

    def __init__(
        self,
        platform: str = "local",
        transport: str = "cli",
        account_id: str = "local-app",
        peer_id: str = "local-user",
        sender_id: str = "local-user",
    ) -> None:
        self._platform = platform
        self._transport = transport
        self._account_id = account_id
        self._peer_id = peer_id
        self._sender_id = sender_id
        self._ids = count(1)

    def receive(self, text: str) -> PlatformEvent:
        """将用户输入的文本转为 PlatformEvent，自动生成 message_id 和时间戳。"""
        message_id = f"local-{next(self._ids)}"
        return PlatformEvent(
            platform=self._platform,
            transport=self._transport,
            event_id=message_id,
            received_at=int(time() * 1000),
            payload={
                "account_id": self._account_id,
                "peer_id": self._peer_id,
                "sender_id": self._sender_id,
                "message_id": message_id,
                "text": text,
            },
        )


class LocalAdapter:
    """本地适配器：将 CLI payload 映射为 InboundMessage，缺省字段用 local 默认值填充。"""

    def to_inbound_message(self, event: PlatformEvent) -> InboundMessage:
        payload = event.payload
        return InboundMessage(
            channel="local",
            account_id=str(payload.get("account_id", "local-app")),
            peer_id=str(payload["peer_id"]),
            sender_id=str(payload["sender_id"]),
            message_id=str(payload["message_id"]),
            text=str(payload.get("text", "")),
            timestamp=event.received_at,
            message_type="text",
            raw=payload,
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(slots=True)
class LocalDelivery:
    """本地投递：将 (消息, 回复) 记录到 sent 列表，不调用任何外部 API。"""

    sent: list[tuple[InboundMessage, AgentReply]] = field(default_factory=list)
    events: asyncio.Queue[tuple[InboundMessage, AgentReply]] = field(
        default_factory=asyncio.Queue
    )

    async def send(self, message: InboundMessage, reply: AgentReply) -> None:
        item = (message, reply)
        self.sent.append(item)
        self.events.put_nowait(item)


class JsonlDelivery:
    """JSONL 投递：将聊天记录按会话追加写入 JSONL 文件。

    文件路径格式：{data_dir}/{session_key}.jsonl
    session_key 中的冒号会替换为下划线以确保文件名合法。
    每条记录一行，包含 role、message_id、session_id、text、timestamp。
    """

    def __init__(self, data_dir: str | Path = "data") -> None:
        self._data_dir = Path(data_dir)

    async def send(self, message: InboundMessage, reply: AgentReply) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        filename = self._safe_filename(message)
        filepath = self._data_dir / filename

        session_id = message.metadata.get("session_id", "")
        lines = [
            _make_record("user", message.message_id, session_id, message.text, message.timestamp),
            _make_record("assistant", message.message_id, session_id, reply.text, message.timestamp),
        ]
        with filepath.open("a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    def _safe_filename(self, message: InboundMessage) -> str:
        """将 session_key 转为安全文件名。"""
        session_key = f"{message.channel}_{message.account_id}_{message.peer_id}"
        return f"{session_key}.jsonl"


def _make_record(
    role: str,
    message_id: str,
    session_id: str,
    text: str,
    timestamp: int,
) -> str:
    """构造一行 JSONL 记录。"""
    record: dict[str, Any] = {
        "role": role,
        "message_id": message_id,
        "session_id": session_id,
        "text": text,
        "timestamp": timestamp,
    }
    return json.dumps(record, ensure_ascii=False)
