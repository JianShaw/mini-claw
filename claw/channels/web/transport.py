"""Web 传输层：将 HTTP 请求参数转为 PlatformEvent。"""

from __future__ import annotations

from time import time
from uuid import uuid4

from claw.types import PlatformEvent


class WebTransport:
    """Web 传输层：Router 调用 receive() 将请求参数转为标准 PlatformEvent。

    与 LocalTransport 同级，实现 Transport protocol。
    支持客户端传入 client_event_id 用于去重（重试/双击场景）。
    """

    def __init__(
        self,
        platform: str = "web",
        transport: str = "http",
        account_id: str = "default",
        peer_id: str = "web",
        sender_id: str = "web",
    ) -> None:
        self._platform = platform
        self._transport = transport
        self._account_id = account_id
        self._peer_id = peer_id
        self._sender_id = sender_id

    def receive(
        self,
        text: str = "",
        *,
        session_id: str | None = None,
        client_event_id: str | None = None,
        extra: dict | None = None,
    ) -> PlatformEvent:
        """将 HTTP 请求参数转为 PlatformEvent。

        Args:
            text: 消息文本
            session_id: 目标会话 ID（Web 端多对话路由用）
            client_event_id: 客户端生成的消息 ID（用于去重），为 None 时 fallback uuid
            extra: 额外 payload 字段
        """
        event_id = client_event_id or f"web-{uuid4().hex[:8]}"
        payload: dict = {
            "account_id": self._account_id,
            "peer_id": self._peer_id,
            "sender_id": self._sender_id,
            "message_id": event_id,
            "text": text,
        }
        if session_id is not None:
            payload["session_id"] = session_id
        if extra:
            payload.update(extra)

        return PlatformEvent(
            platform=self._platform,
            transport=self._transport,
            event_id=event_id,
            received_at=int(time() * 1000),
            payload=payload,
        )
