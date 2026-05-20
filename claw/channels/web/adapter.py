"""Web 适配层：PlatformEvent → InboundMessage。

与 LocalAdapter 同级，实现 Adapter protocol。
优先从 payload 读取 account_id/peer_id/sender_id（未来多用户场景），
无值时 fallback 到模块级默认值（单用户场景）。
"""

from __future__ import annotations

from claw.types import InboundMessage, PlatformEvent

WEB_CHANNEL = "web"
WEB_ACCOUNT_ID = "default"
WEB_PEER_ID = "web"
WEB_SENDER_ID = "web"
# peer_key 格式为 channel:account_id:peer_id，用于唯一标识一个用户
WEB_PEER_KEY = f"{WEB_CHANNEL}:{WEB_ACCOUNT_ID}:{WEB_PEER_ID}"


class WebAdapter:
    """Web 通道适配器：将 PlatformEvent 转为 InboundMessage。

    与 LocalAdapter 同级，实现 Adapter protocol。
    优先从 payload 读取 account_id/peer_id/sender_id（未来多用户场景），
    无值时 fallback 到模块级默认值（单用户场景）。
    """

    def to_inbound_message(self, event: PlatformEvent) -> InboundMessage:
        payload = event.payload
        metadata: dict[str, str] = {}
        if payload.get("session_id"):
            metadata["session_id"] = str(payload["session_id"])
        return InboundMessage(
            channel=WEB_CHANNEL,
            account_id=str(payload.get("account_id", WEB_ACCOUNT_ID)),
            peer_id=str(payload.get("peer_id", WEB_PEER_ID)),
            sender_id=str(payload.get("sender_id", WEB_SENDER_ID)),
            message_id=event.event_id,
            text=str(payload.get("text", "")),
            timestamp=event.received_at,
            message_type="text",
            raw=payload,
            metadata=metadata,
        )

    @staticmethod
    def make_message(
        text: str = "", *, session_id: str | None = None
    ) -> InboundMessage:
        """便捷工厂：构造 PlatformEvent 再委托 to_inbound_message。

        用于 session 管理（create/list/delete）等不需要完整 Transport 管线的场景。
        聊天场景应走 Transport → Processor 完整管线。
        """
        from claw.channels.web.transport import WebTransport

        transport = WebTransport()
        event = transport.receive(text, session_id=session_id)
        return WebAdapter().to_inbound_message(event)
