"""频道处理器：接收 PlatformEvent，编排去重→适配→校验→过滤→网关转发。

在脏数据进入 Gateway 之前，把所有"不干净"的情况处理掉。
通过 error_policy 控制异常处理策略：SWALLOW（webhook）vs RAISE（Web SSE）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import Enum

from claw.ports import Adapter, DedupeStore, Gateway
from claw.types import AgentReply, InboundMessage, PlatformEvent, StreamChunk


class ErrorPolicy(Enum):
    """Processor 异常处理策略。

    SWALLOW: 静默返回 None/空流（webhook 场景，避免触发平台重试）
    RAISE:   向上抛异常（Web SSE 场景，Router 可以 encode_error 发给前端）
    """
    SWALLOW = "swallow"
    RAISE = "raise"


class InMemoryDedupeStore:
    """内存去重存储，开发测试用。生产环境可替换为 Redis（带 TTL）。"""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    async def exists(self, key: str) -> bool:
        return key in self._keys

    async def set(self, key: str, ttl_seconds: int | None = None) -> None:
        self._keys.add(key)


class ChannelProcessor:
    """处理流水线：去重 → 适配转换 → 补充元数据 → 校验 → 过滤 → 转发网关。"""

    def __init__(
        self,
        adapter: Adapter,
        gateway: Gateway,
        dedupe_store: DedupeStore,
        error_policy: ErrorPolicy = ErrorPolicy.SWALLOW,
    ) -> None:
        self._adapter = adapter
        self._gateway = gateway
        self._dedupe_store = dedupe_store
        self._error_policy = error_policy

    async def process(self, event: PlatformEvent) -> AgentReply | None:
        try:
            # 1. 去重检查：相同 event_id 不重复处理
            dedupe_key = self._get_dedupe_key(event)
            if await self._dedupe_store.exists(dedupe_key):
                return None

            # 先标记去重，再处理，防止并发重复
            await self._dedupe_store.set(dedupe_key, ttl_seconds=60 * 60)

            # 2. 通过 Adapter 将平台事件转为标准消息
            inbound = self._adapter.to_inbound_message(event)

            # 3. 补充事件元数据（transport / event_id / received_at）
            inbound.metadata = {
                **inbound.metadata,
                "transport": event.transport,
                "event_id": event.event_id,
                "received_at": event.received_at,
            }

            # 4. 校验必填字段
            reason = self._validate(inbound)
            if reason is not None:
                return None

            # 5. 过滤不需要处理的消息（bot 消息、系统事件）
            if self._should_ignore(inbound):
                return None

            # 6. 转发给 Gateway
            return await self._gateway.handle_inbound_message(inbound)
        except Exception:
            if self._error_policy == ErrorPolicy.RAISE:
                raise
            return None

    async def process_stream(self, event: PlatformEvent) -> AsyncIterator[StreamChunk]:
        """流式处理：复用去重/校验/过滤，通过网关流式转发 StreamChunk。"""
        try:
            dedupe_key = self._get_dedupe_key(event)
            if await self._dedupe_store.exists(dedupe_key):
                return
            await self._dedupe_store.set(dedupe_key, ttl_seconds=60 * 60)

            inbound = self._adapter.to_inbound_message(event)
            inbound.metadata = {
                **inbound.metadata,
                "transport": event.transport,
                "event_id": event.event_id,
                "received_at": event.received_at,
            }

            if self._validate(inbound) is not None:
                return
            if self._should_ignore(inbound):
                return

            async for chunk in self._gateway.handle_stream(inbound):
                yield chunk
        except Exception:
            if self._error_policy == ErrorPolicy.RAISE:
                raise
            return

    def _get_dedupe_key(self, event: PlatformEvent) -> str:
        """用 platform + event_id 拼接去重键。"""
        return f"{event.platform}:{event.event_id}"

    def _validate(self, message: InboundMessage) -> str | None:
        """校验必填字段，返回不通过原因；通过返回 None。"""
        if not message.channel:
            return "missing channel"
        if not message.account_id:
            return "missing account_id"
        if not message.peer_id:
            return "missing peer_id"
        if not message.sender_id:
            return "missing sender_id"
        if not message.message_id:
            return "missing message_id"
        if message.message_type == "text" and not message.text.strip():
            return "empty text"
        return None

    def _should_ignore(self, message: InboundMessage) -> bool:
        """判断是否应忽略该消息（bot 自发消息、系统事件）。"""
        return bool(
            message.metadata.get("is_from_bot")
            or message.metadata.get("event_type") == "system"
        )
