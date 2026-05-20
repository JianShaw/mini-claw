"""Web 通道常量和 InboundMessage 构造器。

所有 Web 流量共用同一套路由字段：channel=web, account_id=default, peer_id=web。
peer_key = "web:default:web"，单用户场景下唯一标识 Web 端。
session_id 通过 metadata 传递，由 Gateway._resolve_session 路由到指定会话。
"""

from __future__ import annotations

from claw.types import InboundMessage

WEB_CHANNEL = "web"
WEB_ACCOUNT_ID = "default"
WEB_PEER_ID = "web"
WEB_SENDER_ID = "web"
WEB_PEER_KEY = f"{WEB_CHANNEL}:{WEB_ACCOUNT_ID}:{WEB_PEER_ID}"


def web_message(
    text: str = "", *, session_id: str | None = None
) -> InboundMessage:
    """构造 Web 端 InboundMessage。

    session_id 为 None 时不设置 metadata（用于路由/列表操作），
    有值时写入 metadata 供 Gateway 路由到指定 session。
    """
    metadata: dict[str, object] = {}
    if session_id is not None:
        metadata["session_id"] = session_id
    return InboundMessage(
        channel=WEB_CHANNEL,
        account_id=WEB_ACCOUNT_ID,
        peer_id=WEB_PEER_ID,
        sender_id=WEB_SENDER_ID,
        message_id="web_msg",
        text=text,
        timestamp=0,
        message_type="text",
        raw=None,
        metadata=metadata,
    )
