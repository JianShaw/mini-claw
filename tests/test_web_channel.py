"""测试 web/backend/channel 模块。"""

from __future__ import annotations

from web.backend.channel import (
    WEB_ACCOUNT_ID,
    WEB_CHANNEL,
    WEB_PEER_ID,
    WEB_PEER_KEY,
    WEB_SENDER_ID,
    web_message,
)


class TestConstants:
    def test_web_channel(self) -> None:
        assert WEB_CHANNEL == "web"

    def test_web_peer_key(self) -> None:
        assert WEB_PEER_KEY == "web:default:web"


class TestWebMessage:
    def test_default_fields(self) -> None:
        msg = web_message()
        assert msg.channel == WEB_CHANNEL
        assert msg.account_id == WEB_ACCOUNT_ID
        assert msg.peer_id == WEB_PEER_ID
        assert msg.sender_id == WEB_SENDER_ID
        assert msg.text == ""
        assert msg.metadata == {}

    def test_with_text(self) -> None:
        msg = web_message("hello")
        assert msg.text == "hello"

    def test_with_session_id(self) -> None:
        msg = web_message("test", session_id="sess_123")
        assert msg.metadata == {"session_id": "sess_123"}

    def test_without_session_id(self) -> None:
        msg = web_message("test")
        assert msg.metadata == {}
