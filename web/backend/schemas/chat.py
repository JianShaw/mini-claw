"""聊天 SSE 相关 Pydantic Schema。"""

from __future__ import annotations

from pydantic import BaseModel


class ChatStreamRequest(BaseModel):
    session_id: str
    text: str
