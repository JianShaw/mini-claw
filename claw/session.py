"""会话管理：session_key 生成、Session 创建、内存存储和 JSONL 持久化存储实现。"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from claw.types import ChatMessage, InboundMessage, Session


def build_session_key(message: InboundMessage) -> str:
    """根据消息的路由字段拼接确定性 key，同一用户在同一频道始终映射到同一会话。"""
    return build_peer_key(message.channel, message.account_id, message.peer_id)


def build_peer_key(channel: str, account_id: str, peer_id: str) -> str:
    """从身份字段构建 peer_key：channel:account_id:peer_id。"""
    return f"{channel}:{account_id}:{peer_id}"


def create_session(message: InboundMessage, agent_id: str = "default-agent") -> Session:
    """根据 InboundMessage 创建新 Session，生成唯一 session_id，用路由字段做 session_key。"""
    return create_session_from_identity(
        channel=message.channel,
        account_id=message.account_id,
        peer_id=message.peer_id,
        sender_id=message.sender_id,
        agent_id=agent_id,
        metadata=message.metadata,
    )


def create_session_from_identity(
    *,
    channel: str,
    account_id: str,
    peer_id: str,
    sender_id: str,
    agent_id: str = "default-agent",
    metadata: dict | None = None,
) -> Session:
    """从身份字段创建新 Session，用于 session 管理场景（非消息驱动）。

    与 create_session(message) 对称，但不需要构造 InboundMessage。
    """
    session_key = build_peer_key(channel, account_id, peer_id)
    return Session(
        session_id=f"sess_{uuid4().hex}",
        session_key=session_key,
        channel=channel,
        account_id=account_id,
        peer_id=peer_id,
        sender_id=sender_id,
        agent_id=agent_id,
        metadata={"channel": channel, **(metadata or {})},
    )


class InMemorySessionStore:
    """内存会话存储，开发测试用。生产环境可替换为 Redis / 数据库实现。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}  # session_id → Session
        self._active: dict[str, str] = {}  # peer_key → active session_id

    async def get(self, session_key: str) -> Session | None:
        """向后兼容：按 peer_key 返回当前活跃 session。"""
        active_id = self._active.get(session_key)
        if active_id:
            return self._sessions.get(active_id)
        return None

    async def save(self, session: Session) -> None:
        """保存 session，首次保存时自动设为活跃。"""
        self._sessions[session.session_id] = session
        peer_key = session.session_key
        if peer_key not in self._active:
            self._active[peer_key] = session.session_id

    async def get_by_id(self, session_id: str) -> Session | None:
        """按 session_id 查找 session。"""
        return self._sessions.get(session_id)

    async def delete(self, session_id: str) -> None:
        """删除 session，若删的是活跃 session 则自动切换。"""
        session = self._sessions.pop(session_id, None)
        if session:
            peer_key = session.session_key
            if self._active.get(peer_key) == session_id:
                # 切换到同 peer 下的另一个 session
                remaining = [
                    s for s in self._sessions.values()
                    if s.session_key == peer_key
                ]
                if remaining:
                    self._active[peer_key] = remaining[-1].session_id
                else:
                    del self._active[peer_key]

    async def list_sessions(self, peer_key: str) -> list[Session]:
        """列出 peer 下的所有 session。"""
        return [s for s in self._sessions.values() if s.session_key == peer_key]

    async def get_active(self, peer_key: str) -> Session | None:
        """获取 peer 当前活跃的 session。"""
        active_id = self._active.get(peer_key)
        if active_id:
            return self._sessions.get(active_id)
        return None

    async def set_active(self, peer_key: str, session_id: str) -> None:
        """设置 peer 的活跃 session。"""
        self._active[peer_key] = session_id

    async def list_peer_keys(self) -> list[str]:
        """返回所有有活跃 session 的 peer_key 列表。"""
        return list(self._active.keys())


class JsonlSessionStore:
    """JSONL 文件持久化会话存储。

    目录结构：
        data_dir/
          index.json              — peer_key → {active, sessions: {id: meta}}
          sess_xxx.jsonl          — 每行一条 ChatMessage 记录

    index.json 使用内存缓存：首次访问时从磁盘加载，后续操作直接修改内存中的 dict，
    仅在变更时写回磁盘，避免每次操作都 read + write JSON 文件。
    JSONL 采用追加写入，compact 后 summary 写入 index，原始记录不删除。
    """

    def __init__(self, data_dir: str | Path = "data/sessions") -> None:
        self._data_dir = Path(data_dir)
        self._index_path = self._data_dir / "index.json"
        # 追踪每个 session 已持久化的消息条数，避免重复追加
        self._saved_count: dict[str, int] = {}
        # 内存缓存，首次访问时懒加载
        self._index: dict | None = None

    # --- index 缓存读写 ---

    def _ensure_index(self) -> dict:
        """获取 index 内存缓存，首次调用时从磁盘加载。"""
        if self._index is None:
            self._index = self._load_index_from_disk()
        return self._index

    def _load_index_from_disk(self) -> dict:
        """从磁盘读取 index.json，文件不存在返回空 dict。"""
        if not self._index_path.exists():
            return {}
        with self._index_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _flush_index(self) -> None:
        """将内存中的 index 写回磁盘。"""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        with self._index_path.open("w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2)

    def _session_path(self, session_id: str) -> Path:
        """获取 session JSONL 文件路径。"""
        return self._data_dir / f"{session_id}.jsonl"

    def _peer_entry(self, peer_key: str) -> dict:
        """获取或创建 peer 在 index 中的条目。"""
        index = self._ensure_index()
        if peer_key not in index:
            index[peer_key] = {"active": "", "sessions": {}}
        return index[peer_key]

    def _session_meta(self, session: Session) -> dict:
        """提取需要存入 index 的 session 元数据。"""
        return {
            "channel": session.channel,
            "account_id": session.account_id,
            "peer_id": session.peer_id,
            "sender_id": session.sender_id,
            "agent_id": session.agent_id,
            "summary": session.summary,
            "history_offset": session.history_offset,
        }

    def _meta_to_session(self, session_id: str, peer_key: str, meta: dict, history: list[ChatMessage] | None = None) -> Session:
        """从 index 元数据构建 Session 对象。"""
        return Session(
            session_id=session_id,
            session_key=peer_key,
            channel=meta["channel"],
            account_id=meta["account_id"],
            peer_id=meta["peer_id"],
            sender_id=meta["sender_id"],
            agent_id=meta["agent_id"],
            history=history or [],
            summary=meta.get("summary"),
            history_offset=meta.get("history_offset", 0),
        )

    # --- JSONL 读写 ---

    def _read_history(self, session_id: str, offset: int = 0) -> list[ChatMessage]:
        """从 JSONL 文件读取历史消息，跳过前 offset 条有效记录。"""
        path = self._session_path(session_id)
        if not path.exists():
            return []
        messages: list[ChatMessage] = []
        count = 0
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if count < offset:
                    count += 1
                    continue
                record = json.loads(line)
                messages.append(ChatMessage(
                    role=record["role"],
                    content=record["content"],
                    ts=record.get("ts"),
                    tool_calls=record.get("tool_calls"),
                    tool_call_id=record.get("tool_call_id"),
                    tool_name=record.get("tool_name"),
                ))
                count += 1
        return messages

    def _append_messages(self, session_id: str, messages: list[ChatMessage]) -> None:
        """追加消息到 JSONL 文件。支持工具调用和工具结果消息的持久化。"""
        if not messages:
            return
        self._data_dir.mkdir(parents=True, exist_ok=True)
        path = self._session_path(session_id)
        with path.open("a", encoding="utf-8") as f:
            for msg in messages:
                record: dict = {"role": msg.role, "content": msg.content}
                if msg.ts is not None:
                    record["ts"] = msg.ts
                if msg.tool_calls is not None:
                    record["tool_calls"] = msg.tool_calls
                if msg.tool_call_id is not None:
                    record["tool_call_id"] = msg.tool_call_id
                if msg.tool_name is not None:
                    record["tool_name"] = msg.tool_name
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # --- SessionStore 接口实现 ---

    async def get(self, session_key: str) -> Session | None:
        """向后兼容：按 peer_key 返回当前活跃 session。"""
        return await self.get_active(session_key)

    async def save(self, session: Session) -> None:
        """保存 session：追加新消息到 JSONL，更新内存 index 并写回磁盘。

        使用 history_offset 防御性计算已持久化数量，避免 _saved_count
        缺失或 stale 时重复追写旧消息。
        """
        offset = session.history_offset
        saved_total = max(self._saved_count.get(session.session_id, offset), offset)
        already_persisted = max(0, min(len(session.history), saved_total - offset))
        new_messages = session.history[already_persisted:]
        self._append_messages(session.session_id, new_messages)
        self._saved_count[session.session_id] = offset + len(session.history)

        # 更新内存中的 index 并写回
        entry = self._peer_entry(session.session_key)
        entry["sessions"][session.session_id] = self._session_meta(session)
        if not entry["active"]:
            entry["active"] = session.session_id
        self._flush_index()

    async def get_by_id(self, session_id: str) -> Session | None:
        """按 session_id 加载完整 session（含 history）。"""
        index = self._ensure_index()
        for peer_key, entry in index.items():
            if session_id in entry["sessions"]:
                meta = entry["sessions"][session_id]
                offset = meta.get("history_offset", 0)
                history = self._read_history(session_id, offset=offset)
                self._saved_count[session_id] = offset + len(history)
                return self._meta_to_session(
                    session_id, peer_key, meta, history
                )
        return None

    async def delete(self, session_id: str) -> None:
        """删除 session：移除 JSONL 文件，更新内存 index 并写回。"""
        # 删除 JSONL 文件
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()

        # 更新内存 index
        index = self._ensure_index()
        for peer_key, entry in index.items():
            if session_id in entry["sessions"]:
                del entry["sessions"][session_id]
                if entry["active"] == session_id:
                    remaining_ids = list(entry["sessions"].keys())
                    entry["active"] = remaining_ids[-1] if remaining_ids else ""
                break
        # 清理空 peer 条目
        empty_keys = [k for k, v in index.items() if not v["sessions"]]
        for k in empty_keys:
            del index[k]
        self._flush_index()

        # 清理内存追踪
        self._saved_count.pop(session_id, None)

    async def list_sessions(self, peer_key: str) -> list[Session]:
        """列出 peer 下的所有 session（轻量，不加载 history）。"""
        index = self._ensure_index()
        entry = index.get(peer_key)
        if not entry:
            return []
        return [
            self._meta_to_session(sid, peer_key, meta)
            for sid, meta in entry["sessions"].items()
        ]

    async def get_active(self, peer_key: str) -> Session | None:
        """获取 peer 当前活跃的 session（含 history）。"""
        index = self._ensure_index()
        entry = index.get(peer_key)
        if not entry or not entry["active"]:
            return None
        active_id = entry["active"]
        if active_id not in entry["sessions"]:
            return None
        meta = entry["sessions"][active_id]
        offset = meta.get("history_offset", 0)
        history = self._read_history(active_id, offset=offset)
        self._saved_count[active_id] = offset + len(history)
        return self._meta_to_session(active_id, peer_key, meta, history)

    async def set_active(self, peer_key: str, session_id: str) -> None:
        """设置 peer 的活跃 session。"""
        entry = self._peer_entry(peer_key)
        entry["active"] = session_id
        self._flush_index()

    async def list_peer_keys(self) -> list[str]:
        """返回所有有活跃 session 的 peer_key 列表。"""
        index = self._ensure_index()
        return [k for k, v in index.items() if v.get("active")]
