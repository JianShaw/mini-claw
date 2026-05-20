"""测试 Web Channel 四件套：Transport + Adapter + Delivery + Processor error policy。"""

from __future__ import annotations

import json

import pytest

from claw.channels.web.adapter import (
    WEB_ACCOUNT_ID,
    WEB_CHANNEL,
    WEB_PEER_ID,
    WEB_PEER_KEY,
    WEB_SENDER_ID,
    WebAdapter,
)
from claw.channels.web.delivery import SseEncoder
from claw.channels.web.transport import WebTransport
from claw.processor import ChannelProcessor, ErrorPolicy, InMemoryDedupeStore
from claw.types import InboundMessage, PlatformEvent, StreamChunk


class TestWebTransport:
    def test_receive_creates_platform_event(self):
        transport = WebTransport()
        event = transport.receive("hello")
        assert isinstance(event, PlatformEvent)
        assert event.platform == "web"
        assert event.transport == "http"

    def test_receive_sets_payload_fields(self):
        transport = WebTransport()
        event = transport.receive("hello")
        assert event.payload["text"] == "hello"
        assert event.payload["account_id"] == "default"
        assert event.payload["peer_id"] == "web"
        assert event.payload["sender_id"] == "web"

    def test_receive_with_session_id(self):
        transport = WebTransport()
        event = transport.receive("hello", session_id="sess_123")
        assert event.payload["session_id"] == "sess_123"

    def test_receive_uses_client_event_id_when_provided(self):
        transport = WebTransport()
        event = transport.receive("hello", client_event_id="client-msg-42")
        assert event.event_id == "client-msg-42"

    def test_receive_falls_back_to_uuid_when_no_client_event_id(self):
        transport = WebTransport()
        event = transport.receive("hello")
        assert event.event_id.startswith("web-")

    def test_receive_same_client_event_id_produces_same_id(self):
        transport = WebTransport()
        e1 = transport.receive("a", client_event_id="dup-1")
        e2 = transport.receive("b", client_event_id="dup-1")
        assert e1.event_id == e2.event_id == "dup-1"

    def test_receive_default_text_is_empty(self):
        transport = WebTransport()
        event = transport.receive()
        assert event.payload["text"] == ""

    def test_receive_with_extra(self):
        transport = WebTransport()
        event = transport.receive("x", extra={"foo": "bar"})
        assert event.payload["foo"] == "bar"


class TestWebAdapter:
    def test_to_inbound_message_reads_from_payload(self):
        adapter = WebAdapter()
        event = PlatformEvent(
            platform="web",
            transport="http",
            event_id="ev-1",
            received_at=1000,
            payload={
                "account_id": "custom-account",
                "peer_id": "custom-peer",
                "sender_id": "custom-sender",
                "message_id": "ev-1",
                "text": "hello world",
            },
        )
        msg = adapter.to_inbound_message(event)
        assert msg.channel == WEB_CHANNEL
        assert msg.account_id == "custom-account"
        assert msg.peer_id == "custom-peer"
        assert msg.sender_id == "custom-sender"
        assert msg.text == "hello world"
        assert msg.message_id == "ev-1"
        assert msg.timestamp == 1000

    def test_to_inbound_message_falls_back_to_defaults(self):
        adapter = WebAdapter()
        event = PlatformEvent(
            platform="web",
            transport="http",
            event_id="ev-2",
            received_at=2000,
            payload={"text": "fallback test"},
        )
        msg = adapter.to_inbound_message(event)
        assert msg.account_id == WEB_ACCOUNT_ID
        assert msg.peer_id == WEB_PEER_ID
        assert msg.sender_id == WEB_SENDER_ID

    def test_to_inbound_message_extracts_session_id(self):
        adapter = WebAdapter()
        event = PlatformEvent(
            platform="web",
            transport="http",
            event_id="ev-3",
            received_at=3000,
            payload={"text": "hi", "session_id": "sess_456"},
        )
        msg = adapter.to_inbound_message(event)
        assert msg.metadata["session_id"] == "sess_456"

    def test_to_inbound_message_no_session_id_in_metadata(self):
        adapter = WebAdapter()
        event = PlatformEvent(
            platform="web",
            transport="http",
            event_id="ev-4",
            received_at=4000,
            payload={"text": "no session"},
        )
        msg = adapter.to_inbound_message(event)
        assert "session_id" not in msg.metadata


class TestWebAdapterMakeMessage:
    def test_make_message_returns_inbound_message(self):
        msg = WebAdapter.make_message("hello")
        assert isinstance(msg, InboundMessage)
        assert msg.channel == WEB_CHANNEL
        assert msg.text == "hello"

    def test_make_message_with_session_id(self):
        msg = WebAdapter.make_message("test", session_id="sess_789")
        assert msg.metadata["session_id"] == "sess_789"

    def test_make_message_defaults(self):
        msg = WebAdapter.make_message()
        assert msg.text == ""
        assert msg.metadata == {}


class TestWebConstants:
    def test_web_channel(self):
        assert WEB_CHANNEL == "web"

    def test_web_peer_key(self):
        assert WEB_PEER_KEY == "web:default:web"

    def test_web_account_id(self):
        assert WEB_ACCOUNT_ID == "default"

    def test_web_peer_id(self):
        assert WEB_PEER_ID == "web"

    def test_web_sender_id(self):
        assert WEB_SENDER_ID == "web"


class TestSseEncoder:
    def test_encode_chunk(self):
        chunk = StreamChunk(type="content", text="hello")
        result = SseEncoder.encode_chunk(chunk)
        assert result["data"] == json.dumps(
            {"type": "content", "text": "hello"}, ensure_ascii=False
        )

    def test_encode_chunk_thinking(self):
        chunk = StreamChunk(type="thinking", text="hmm...")
        result = SseEncoder.encode_chunk(chunk)
        parsed = json.loads(result["data"])
        assert parsed["type"] == "thinking"
        assert parsed["text"] == "hmm..."

    def test_encode_error(self):
        result = SseEncoder.encode_error(ValueError("bad input"))
        parsed = json.loads(result["data"])
        assert parsed["type"] == "error"
        assert "bad input" in parsed["text"]

    def test_encode_done(self):
        result = SseEncoder.encode_done()
        assert result["data"] == "[DONE]"


class _FakeGateway:
    """Gateway that always raises to test error propagation."""
    def __init__(self, error: Exception):
        self._error = error

    async def handle_inbound_message(self, message):
        raise self._error

    async def handle_stream(self, message):
        raise self._error
        yield  # unreachable, for async generator


class _FakeAdapter:
    def to_inbound_message(self, event):
        return InboundMessage(
            channel="web",
            account_id="default",
            peer_id="web",
            sender_id="web",
            message_id=event.event_id,
            text="test",
            timestamp=0,
            message_type="text",
            raw=None,
        )


class TestProcessorErrorPolicy:
    @pytest.mark.asyncio
    async def test_swallow_returns_none_on_error(self):
        processor = ChannelProcessor(
            adapter=_FakeAdapter(),
            gateway=_FakeGateway(RuntimeError("boom")),
            dedupe_store=InMemoryDedupeStore(),
            error_policy=ErrorPolicy.SWALLOW,
        )
        event = PlatformEvent(
            platform="web",
            transport="http",
            event_id="ev-err",
            received_at=0,
            payload={},
        )
        result = await processor.process(event)
        assert result is None

    @pytest.mark.asyncio
    async def test_raise_propagates_error(self):
        processor = ChannelProcessor(
            adapter=_FakeAdapter(),
            gateway=_FakeGateway(RuntimeError("boom")),
            dedupe_store=InMemoryDedupeStore(),
            error_policy=ErrorPolicy.RAISE,
        )
        event = PlatformEvent(
            platform="web",
            transport="http",
            event_id="ev-err",
            received_at=0,
            payload={},
        )
        with pytest.raises(RuntimeError, match="boom"):
            await processor.process(event)

    @pytest.mark.asyncio
    async def test_swallow_on_stream_returns_empty(self):
        processor = ChannelProcessor(
            adapter=_FakeAdapter(),
            gateway=_FakeGateway(RuntimeError("boom")),
            dedupe_store=InMemoryDedupeStore(),
            error_policy=ErrorPolicy.SWALLOW,
        )
        event = PlatformEvent(
            platform="web",
            transport="http",
            event_id="ev-stream-err",
            received_at=0,
            payload={},
        )
        chunks = [c async for c in processor.process_stream(event)]
        assert chunks == []

    @pytest.mark.asyncio
    async def test_raise_on_stream_propagates_error(self):
        processor = ChannelProcessor(
            adapter=_FakeAdapter(),
            gateway=_FakeGateway(RuntimeError("boom")),
            dedupe_store=InMemoryDedupeStore(),
            error_policy=ErrorPolicy.RAISE,
        )
        event = PlatformEvent(
            platform="web",
            transport="http",
            event_id="ev-stream-err",
            received_at=0,
            payload={},
        )
        with pytest.raises(RuntimeError, match="boom"):
            async for _ in processor.process_stream(event):
                pass

    @pytest.mark.asyncio
    async def test_default_policy_is_swallow(self):
        processor = ChannelProcessor(
            adapter=_FakeAdapter(),
            gateway=_FakeGateway(RuntimeError("boom")),
            dedupe_store=InMemoryDedupeStore(),
        )
        event = PlatformEvent(
            platform="web", transport="http", event_id="ev-def", received_at=0, payload={}
        )
        result = await processor.process(event)
        assert result is None
