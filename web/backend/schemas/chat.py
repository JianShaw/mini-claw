"""聊天 SSE 相关 Pydantic Schema。"""

from __future__ import annotations

from pydantic import BaseModel


class ChatStreamRequest(BaseModel):
    session_id: str
    text: str
    # 客户端生成的消息 ID，用于去重（重试/双击场景）；为 None 时服务端 fallback uuid
    client_event_id: str | None = None
