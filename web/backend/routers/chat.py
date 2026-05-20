"""聊天 SSE 流式 API。"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

from claw.gateway import RuntimeGateway
from web.backend.channel import web_message
from web.backend.schemas.chat import ChatStreamRequest

router = APIRouter(prefix="/chat", tags=["chat"])


def get_gateway() -> RuntimeGateway:
    """由 app.py 通过 dependency_overrides 注入。"""
    raise NotImplementedError("Gateway not configured")


@router.post("/stream")
async def chat_stream(
    request: ChatStreamRequest,
    gw: RuntimeGateway = Depends(get_gateway),
) -> EventSourceResponse:
    """SSE 流式聊天端点。"""

    async def event_generator():
        logger.info("event_generator entered, session_id=%s", request.session_id)
        message = web_message(request.text, session_id=request.session_id)
        try:
            async for chunk in gw.handle_stream(message):
                data = json.dumps({"type": chunk.type, "text": chunk.text}, ensure_ascii=False)
                yield {"data": data}
            logger.info("event_generator loop finished normally")
        except Exception as e:
            logger.warning("event_generator exception: %s", e)
            yield {"data": json.dumps({"type": "error", "text": str(e)}, ensure_ascii=False)}
        logger.info("event_generator yielding [DONE]")
        yield {"data": "[DONE]"}

    return EventSourceResponse(event_generator())
