"""TaskContext：调度器和系统 task 之间的边界对象。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from claw.types import InboundMessage, Session

if True:  # 避免循环导入
    from claw.gateway import RuntimeGateway
    from claw.memory.manager import MemoryManager
    from claw.ports import SessionStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TaskContext:
    """调度器和系统 task 之间的边界对象。

    仅用于系统任务（handler 直调模式），LLM 任务走 gateway 全链路，
    不经过 TaskContext。

    关键约束：无 active session 时返回 None / skipped，
    绝不隐式创建新 session。
    """

    _session_store: SessionStore
    _memory_manager: MemoryManager | None
    _gateway: RuntimeGateway | None
    _event_payloads: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    # --- peer 发现 ---

    async def all_peers(self) -> list[str]:
        """返回所有有活跃 session 的 peer_key 列表。"""
        return await self._session_store.list_peer_keys()

    # --- session 操作 ---

    async def active_session(self, peer_key: str) -> Session | None:
        """获取指定 peer 的活跃 session。无活跃 session 返回 None。"""
        return await self._session_store.get_active(peer_key)

    async def all_active_sessions(self) -> list[tuple[str, Session]]:
        """获取所有 peer 的活跃 session，返回 (peer_key, Session) 列表。"""
        peers = await self.all_peers()
        result: list[tuple[str, Session]] = []
        for peer_key in peers:
            session = await self._session_store.get_active(peer_key)
            if session is not None:
                result.append((peer_key, session))
        return result

    # --- memory 操作 ---

    @property
    def memory_manager(self) -> MemoryManager | None:
        """直接访问 memory manager（用于 distill 等不依赖 session 的操作）。"""
        return self._memory_manager

    async def update_daily(self, session: Session, *, force: bool = False) -> bool:
        """更新指定 session 的 daily memory。"""
        if self._memory_manager is None:
            return False
        return await self._memory_manager.maybe_update_daily(session, force=force)

    async def distill(self) -> int:
        """蒸馏长期记忆，返回新增条数。"""
        if self._memory_manager is None:
            return 0
        result = await self._memory_manager.distill_daily_to_long_term()
        return result.added

    # --- session 压缩 ---

    async def compact(self, peer_key: str) -> str | None:
        """压缩指定 peer 的活跃 session。

        内部构造 routing message 定位 peer（gateway.compact_session 需要）。
        无活跃 session 时返回 None，不创建新 session。
        """
        if self._gateway is None:
            return None
        parts = peer_key.split(":", 2)
        msg = InboundMessage(
            channel=parts[0] if len(parts) > 0 else "local",
            account_id=parts[1] if len(parts) > 1 else "app",
            peer_id=parts[2] if len(parts) > 2 else "user",
            sender_id="scheduler",
            message_id="_compact",
            text="",
            timestamp=0,
            message_type="text",
            raw=None,
        )
        return await self._gateway.compact_session(msg)

    # --- event payload ---

    def last_event_payload(self, event_name: str, *, key: str) -> str | None:
        """获取指定事件最后一次 emit 携带的某个 key 的值。"""
        payloads = self._event_payloads.get(event_name)
        if not payloads:
            return None
        value = payloads[-1].get(key)
        return str(value) if value is not None else None
