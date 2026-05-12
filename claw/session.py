"""会话管理：session_key 生成、Session 创建、内存存储实现。"""

from __future__ import annotations

from uuid import uuid4

from claw.types import InboundMessage, Session


def build_session_key(message: InboundMessage) -> str:
    """根据消息的路由字段拼接确定性 key，同一用户在同一频道始终映射到同一会话。"""
    return f"{message.channel}:{message.account_id}:{message.peer_id}"


def create_session(message: InboundMessage, agent_id: str = "default-agent") -> Session:
    """根据 InboundMessage 创建新 Session，生成唯一 session_id，用路由字段做 session_key。"""
    session_key = build_session_key(message)
    return Session(
        session_id=f"sess_{uuid4().hex}",
        session_key=session_key,
        channel=message.channel,
        account_id=message.account_id,
        peer_id=message.peer_id,
        sender_id=message.sender_id,
        agent_id=agent_id,
        metadata={
            "channel": message.channel,
            **message.metadata,
        },
    )


class InMemorySessionStore:
    """内存会话存储，开发测试用。生产环境可替换为 Redis / 数据库实现。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    async def get(self, session_key: str) -> Session | None:
        return self._sessions.get(session_key)

    async def save(self, session: Session) -> None:
        self._sessions[session.session_key] = session
