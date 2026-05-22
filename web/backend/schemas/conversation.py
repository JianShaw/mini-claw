"""对话相关 Pydantic Schema。"""

from __future__ import annotations

from pydantic import BaseModel


class ChatMessageSchema(BaseModel):
    role: str
    content: str
    ts: int | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None


class ConversationSchema(BaseModel):
    session_id: str
    agent_id: str
    channel: str = ""
    summary: str | None = None
    messages: list[ChatMessageSchema] = []
    created_at: str = ""
    updated_at: str = ""


class ConversationListItem(BaseModel):
    session_id: str
    agent_id: str
    channel: str = ""
    summary: str | None = None
    message_count: int = 0
    session_type: str | None = None  # "scheduled" | None
    created_at: str = ""
    updated_at: str = ""


class CreateConversationRequest(BaseModel):
    agent_id: str
