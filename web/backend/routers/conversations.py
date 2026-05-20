"""对话 REST API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from claw.channels.web.adapter import (
    WEB_ACCOUNT_ID,
    WEB_CHANNEL,
    WEB_PEER_ID,
    WEB_PEER_KEY,
    WEB_SENDER_ID,
)
from claw.gateway import RuntimeGateway
from web.backend.schemas.conversation import (
    ChatMessageSchema,
    ConversationListItem,
    ConversationSchema,
    CreateConversationRequest,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


def get_gateway() -> RuntimeGateway:
    """由 app.py 通过 dependency_overrides 注入。"""
    raise NotImplementedError("Gateway not configured")


@router.post("", response_model=ConversationSchema, status_code=201)
async def create_conversation(
    request: CreateConversationRequest,
    gw: RuntimeGateway = Depends(get_gateway),
) -> ConversationSchema:
    """创建对话：通过 Gateway 绑定 agent_id。"""
    session = await gw.create_session_for_agent(
        WEB_PEER_KEY, request.agent_id,
        channel=WEB_CHANNEL, account_id=WEB_ACCOUNT_ID,
        peer_id=WEB_PEER_ID, sender_id=WEB_SENDER_ID,
    )
    return _session_to_schema(session)


@router.get("", response_model=list[ConversationListItem])
async def list_conversations(
    gw: RuntimeGateway = Depends(get_gateway),
) -> list[ConversationListItem]:
    sessions = await gw.list_sessions(WEB_PEER_KEY)
    return [_session_to_list_item(s) for s in sessions]


@router.get("/{session_id}", response_model=ConversationSchema)
async def get_conversation(
    session_id: str,
    gw: RuntimeGateway = Depends(get_gateway),
) -> ConversationSchema:
    session = await gw.get_session_by_id(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Conversation not found: {session_id}")
    return _session_to_schema(session, include_messages=True)


@router.delete("/{session_id}", status_code=204)
async def delete_conversation(
    session_id: str,
    gw: RuntimeGateway = Depends(get_gateway),
) -> None:
    await gw.delete_session(WEB_PEER_KEY, session_id)


def _session_to_schema(session, include_messages: bool = False) -> ConversationSchema:
    messages = []
    if include_messages:
        for m in session.history:
            messages.append(ChatMessageSchema(
                role=m.role, content=m.content,
                tool_calls=m.tool_calls, tool_call_id=m.tool_call_id,
                tool_name=m.tool_name,
            ))
    return ConversationSchema(
        session_id=session.session_id,
        agent_id=session.agent_id,
        channel=session.channel,
        summary=session.summary,
        messages=messages,
    )


def _session_to_list_item(session) -> ConversationListItem:
    # SQLite list_sessions 用 COUNT 查询填充 metadata["message_count"]
    # InMemory session_store 则直接用 len(history)
    message_count = session.metadata.get("message_count", len(session.history))
    return ConversationListItem(
        session_id=session.session_id,
        agent_id=session.agent_id,
        channel=session.channel,
        summary=session.summary,
        message_count=message_count,
    )
