"""对话 REST API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

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
from web.backend.services.task_service import TaskService

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
    request: Request,
    type: str | None = None,
    gw: RuntimeGateway = Depends(get_gateway),
) -> list[ConversationListItem]:
    """列出对话。type 过滤：'normal'=普通对话，'scheduled'=定时推送，None=全部。"""
    sessions = await gw.list_sessions(WEB_PEER_KEY)

    # 定时推送 session 存储在 sched:xxx 的 peer_key 下，不在 WEB_PEER_KEY 中
    # 需要从 TaskService 获取关联 session 并补充到列表
    task_service: TaskService | None = getattr(request.app.state, "task_service", None)
    if task_service is not None:
        seen_ids = {s.session_id for s in sessions}
        for sid in task_service.get_scheduled_session_ids():
            if sid in seen_ids:
                continue
            session = await gw.get_session_by_id(sid)
            if session is not None:
                sessions.append(session)
                seen_ids.add(sid)

    if type == "scheduled":
        sessions = [s for s in sessions if s.metadata.get("session_type") == "scheduled"]
    elif type == "normal":
        sessions = [s for s in sessions if s.metadata.get("session_type") != "scheduled"]
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
                ts=m.ts,
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
    message_count = session.metadata.get("message_count", len(session.history))
    return ConversationListItem(
        session_id=session.session_id,
        agent_id=session.agent_id,
        channel=session.channel,
        summary=session.summary,
        message_count=message_count,
        session_type=session.metadata.get("session_type"),
    )
