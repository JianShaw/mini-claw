"""SQLite 会话存储：将 Session 和 ChatMessage 持久化到 sessions / session_messages 表。

遵循 SqliteAgentStore / SqliteExpertStore 同一模式：
构造函数接收 sqlite3.Connection，INSERT OR REPLACE upsert，
复杂字段（metadata, tool_calls）JSON 序列化。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from claw.types import ChatMessage, Session


def _now_iso() -> str:
    """UTC 当前时间 ISO 格式。"""
    return datetime.now(timezone.utc).isoformat()


class SqliteSessionStore:
    """SQLite 持久化会话存储，实现 SessionStore Protocol。

    目录结构（在 data/mini_claw.sqlite 中）：
        sessions         — session 元数据（peer_key, agent_id, summary, offset 等）
        session_messages — 消息历史（append-only，offset 跳过已压缩部分）
        active_sessions  — peer_key → active session_id 映射
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        # 追踪每个 session 已持久化的消息总条数，防止重复追写
        self._persisted_count: dict[str, int] = {}

    # ---- 行 ↔ 对象转换 ----

    def _row_to_session(
        self, row: sqlite3.Row, *, include_history: bool = True
    ) -> Session:
        """将 sessions 表行转为 Session 对象。

        include_history=False 时跳过消息加载，用于 list_sessions 轻量查询。
        """
        session = Session(
            session_id=row["session_id"],
            session_key=row["session_key"],
            channel=row["channel"],
            account_id=row["account_id"],
            peer_id=row["peer_id"],
            sender_id=row["sender_id"],
            agent_id=row["agent_id"],
            metadata=json.loads(row["metadata_json"]),
            summary=row["summary"],
            history_offset=row["history_offset"],
        )
        if include_history:
            session.history = self._load_messages(
                session.session_id, offset=session.history_offset
            )
            self._persisted_count[session.session_id] = (
                session.history_offset + len(session.history)
            )
        return session

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> ChatMessage:
        """将 session_messages 表行转为 ChatMessage。"""
        tool_calls_raw = row["tool_calls_json"]
        return ChatMessage(
            role=row["role"],
            content=row["content"],
            ts=row["ts"],
            tool_calls=json.loads(tool_calls_raw) if tool_calls_raw else None,
            tool_call_id=row["tool_call_id"],
            tool_name=row["tool_name"],
            reasoning_content=row["reasoning_content"],
        )

    @staticmethod
    def _message_to_tuple(session_id: str, msg: ChatMessage) -> tuple:
        """将 ChatMessage 转为 INSERT 参数元组。"""
        return (
            session_id,
            msg.role,
            msg.content,
            json.dumps(msg.tool_calls, ensure_ascii=False)
            if msg.tool_calls
            else None,
            msg.tool_call_id,
            msg.tool_name,
            msg.reasoning_content,
            msg.ts,
            _now_iso(),
        )

    # ---- 消息读写 ----

    def _load_messages(
        self, session_id: str, offset: int = 0
    ) -> list[ChatMessage]:
        """从 session_messages 加载消息，跳过前 offset 条（已压缩部分）。"""
        rows = self._conn.execute(
            "SELECT * FROM session_messages WHERE session_id = ? "
            "ORDER BY id LIMIT -1 OFFSET ?",
            (session_id, offset),
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    # ---- SessionStore Protocol 实现 ----

    async def get(self, session_key: str) -> Session | None:
        """向后兼容：按 peer_key 返回当前活跃 session。"""
        return await self.get_active(session_key)

    async def save(self, session: Session) -> None:
        """保存 session：upsert 元数据 + 增量追加新消息 + 自动设为活跃。

        消息追加算法（与 JsonlSessionStore 对称）：
        1. 通过 _persisted_count 计算已持久化条数
        2. 只 INSERT 尚未写入的新消息
        3. 首次保存时自动设为活跃 session
        """
        now = _now_iso()
        offset = session.history_offset

        # 计算已持久化的消息数，防御性取 max
        persisted = max(
            self._persisted_count.get(session.session_id, offset), offset
        )
        already_persisted = max(
            0, min(len(session.history), persisted - offset)
        )
        new_messages = session.history[already_persisted:]

        with self._conn:
            # Upsert session 元数据
            # 注意：不能用 INSERT OR REPLACE，因为它会 DELETE 再 INSERT，
            # 触发 CASCADE 删除 session_messages。改用 ON CONFLICT DO UPDATE。
            self._conn.execute(
                """INSERT INTO sessions (
                    session_id, session_key, channel, account_id, peer_id, sender_id,
                    agent_id, metadata_json, summary, history_offset, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    session_key=excluded.session_key,
                    channel=excluded.channel,
                    account_id=excluded.account_id,
                    peer_id=excluded.peer_id,
                    sender_id=excluded.sender_id,
                    agent_id=excluded.agent_id,
                    metadata_json=excluded.metadata_json,
                    summary=excluded.summary,
                    history_offset=excluded.history_offset,
                    updated_at=excluded.updated_at""",
                (
                    session.session_id,
                    session.session_key,
                    session.channel,
                    session.account_id,
                    session.peer_id,
                    session.sender_id,
                    session.agent_id,
                    json.dumps(session.metadata, ensure_ascii=False),
                    session.summary,
                    session.history_offset,
                    now,
                    now,
                ),
            )

            # 增量追加新消息
            if new_messages:
                self._conn.executemany(
                    """INSERT INTO session_messages (
                        session_id, role, content, tool_calls_json,
                        tool_call_id, tool_name, reasoning_content, ts, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        self._message_to_tuple(session.session_id, m)
                        for m in new_messages
                    ],
                )

            # 更新内存追踪
            self._persisted_count[session.session_id] = (
                offset + len(session.history)
            )

            # 首次保存时自动设为活跃
            existing = self._conn.execute(
                "SELECT 1 FROM active_sessions WHERE session_key = ?",
                (session.session_key,),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    "INSERT OR REPLACE INTO active_sessions "
                    "(session_key, session_id, updated_at) VALUES (?, ?, ?)",
                    (session.session_key, session.session_id, now),
                )

    async def get_by_id(self, session_id: str) -> Session | None:
        """按 session_id 加载完整 session（含历史消息）。"""
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_session(row, include_history=True)

    async def delete(self, session_id: str) -> None:
        """删除 session：CASCADE 自动删消息和 active_sessions 条目。

        注意：active_sessions 有 FOREIGN KEY(session_id) REFERENCES sessions
        ON DELETE CASCADE，所以 DELETE sessions 会同时删掉 active_sessions 行。
        必须在 DELETE 前读取活跃状态，DELETE 后重新写入。
        """
        # 在 DELETE 前读取 session 和活跃状态（CASCADE 会删 active_sessions）
        row = self._conn.execute(
            "SELECT session_key FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return

        peer_key = row["session_key"]

        # 记住当前活跃 session 是否是被删除的那个
        active_row = self._conn.execute(
            "SELECT session_id FROM active_sessions WHERE session_key = ?",
            (peer_key,),
        ).fetchone()
        was_active = active_row is not None and active_row["session_id"] == session_id

        # CASCADE：删除 sessions 行 → 自动删 session_messages + active_sessions
        with self._conn:
            self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )

            # 如果被删除的是活跃 session，找同 peer 下的替代并重新写入 active
            if was_active:
                remaining = self._conn.execute(
                    "SELECT session_id FROM sessions WHERE session_key = ? "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (peer_key,),
                ).fetchone()
                if remaining:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO active_sessions "
                        "(session_key, session_id, updated_at) VALUES (?, ?, ?)",
                        (peer_key, remaining["session_id"], _now_iso()),
                    )

        self._persisted_count.pop(session_id, None)

    async def list_sessions(self, peer_key: str) -> list[Session]:
        """列出 peer 下的所有 session（轻量，不加载历史消息）。"""
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE session_key = ? ORDER BY updated_at",
            (peer_key,),
        ).fetchall()
        return [self._row_to_session(r, include_history=False) for r in rows]

    async def get_active(self, peer_key: str) -> Session | None:
        """获取 peer 当前活跃 session（含历史消息）。"""
        active_row = self._conn.execute(
            "SELECT session_id FROM active_sessions WHERE session_key = ?",
            (peer_key,),
        ).fetchone()
        if active_row is None:
            return None
        return await self.get_by_id(active_row["session_id"])

    async def set_active(self, peer_key: str, session_id: str) -> None:
        """设置 peer 的活跃 session。"""
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO active_sessions "
                "(session_key, session_id, updated_at) VALUES (?, ?, ?)",
                (peer_key, session_id, _now_iso()),
            )

    async def list_peer_keys(self) -> list[str]:
        """返回所有有活跃 session 的 peer_key 列表。"""
        rows = self._conn.execute(
            "SELECT session_key FROM active_sessions"
        ).fetchall()
        return [r["session_key"] for r in rows]
