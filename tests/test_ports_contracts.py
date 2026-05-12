"""端口/接口契约测试：验证每个 Protocol 都能被 Fake 实现满足。"""

from __future__ import annotations

import pytest

from claw.ports import Adapter, AgentRunner, DedupeStore, Delivery, Gateway, SessionStore, Transport
from claw.types import AgentReply, InboundMessage, PlatformEvent, Session


class FakeTransport:
    """满足 Transport Protocol 的最小实现。"""
    def receive(self, text: str) -> PlatformEvent:
        return PlatformEvent(
            platform="test",
            transport="test",
            event_id="e1",
            received_at=0,
            payload={"text": text},
        )


class FakeAdapter:
    """满足 Adapter Protocol 的最小实现。"""
    def to_inbound_message(self, event: PlatformEvent) -> InboundMessage:
        return InboundMessage(
            channel="test",
            account_id="app",
            peer_id="user",
            sender_id="user",
            message_id="1",
            text="hi",
            timestamp=0,
            message_type="text",
            raw=None,
        )


class FakeGateway:
    """满足 Gateway Protocol 的最小实现。"""
    async def handle_inbound_message(self, message: InboundMessage) -> AgentReply:
        return AgentReply(text="ok")


class FakeDedupeStore:
    """满足 DedupeStore Protocol 的最小实现。"""
    async def exists(self, key: str) -> bool:
        return False

    async def set(self, key: str, ttl_seconds: int | None = None) -> None:
        pass


class FakeSessionStore:
    """满足 SessionStore Protocol 的最小实现。"""
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._active: dict[str, str] = {}

    async def get(self, session_key: str) -> Session | None:
        active_id = self._active.get(session_key)
        if active_id:
            return self._sessions.get(active_id)
        return None

    async def save(self, session: Session) -> None:
        self._sessions[session.session_id] = session
        if session.session_key not in self._active:
            self._active[session.session_key] = session.session_id

    async def get_by_id(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    async def delete(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            peer_key = session.session_key
            if self._active.get(peer_key) == session_id:
                remaining = [s for s in self._sessions.values() if s.session_key == peer_key]
                if remaining:
                    self._active[peer_key] = remaining[-1].session_id
                else:
                    del self._active[peer_key]

    async def list_sessions(self, peer_key: str) -> list[Session]:
        return [s for s in self._sessions.values() if s.session_key == peer_key]

    async def get_active(self, peer_key: str) -> Session | None:
        active_id = self._active.get(peer_key)
        if active_id:
            return self._sessions.get(active_id)
        return None

    async def set_active(self, peer_key: str, session_id: str) -> None:
        self._active[peer_key] = session_id


class FakeAgentRunner:
    """满足 AgentRunner Protocol 的最小实现。"""
    async def run(self, session: Session, message: InboundMessage) -> AgentReply:
        return AgentReply(text="ok")


class FakeDelivery:
    """满足 Delivery Protocol 的最小实现。"""
    async def send(self, message: InboundMessage, reply: AgentReply) -> None:
        pass


def test_transport_protocol() -> None:
    """FakeTransport 可以赋值给 Transport 类型的变量。"""
    transport: Transport = FakeTransport()
    assert isinstance(transport, FakeTransport)


def test_adapter_protocol() -> None:
    """FakeAdapter 可以赋值给 Adapter 类型的变量。"""
    adapter: Adapter = FakeAdapter()
    assert isinstance(adapter, FakeAdapter)


def test_gateway_protocol() -> None:
    """FakeGateway 可以赋值给 Gateway 类型的变量。"""
    gateway: Gateway = FakeGateway()
    assert isinstance(gateway, FakeGateway)


def test_dedupe_store_protocol() -> None:
    """FakeDedupeStore 可以赋值给 DedupeStore 类型的变量。"""
    store: DedupeStore = FakeDedupeStore()
    assert isinstance(store, FakeDedupeStore)


def test_session_store_protocol() -> None:
    """FakeSessionStore 可以赋值给 SessionStore 类型的变量。"""
    store: SessionStore = FakeSessionStore()
    assert isinstance(store, FakeSessionStore)


def test_agent_runner_protocol() -> None:
    """FakeAgentRunner 可以赋值给 AgentRunner 类型的变量。"""
    runner: AgentRunner = FakeAgentRunner()
    assert isinstance(runner, FakeAgentRunner)


def test_delivery_protocol() -> None:
    """FakeDelivery 可以赋值给 Delivery 类型的变量。"""
    delivery: Delivery = FakeDelivery()
    assert isinstance(delivery, FakeDelivery)
