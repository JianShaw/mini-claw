"""聊天 SSE 流式 API。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

from claw.channels.web.delivery import SseEncoder
from claw.channels.web.transport import WebTransport
from claw.gateway import RuntimeGateway
from claw.processor import ChannelProcessor
from web.backend.schemas.chat import ChatStreamRequest

router = APIRouter(prefix="/chat", tags=["chat"])


def get_gateway() -> RuntimeGateway:
    """由 app.py 通过 dependency_overrides 注入。"""
    raise NotImplementedError("Gateway not configured")


@router.post("/stream")
async def chat_stream(
    request: ChatStreamRequest,
    req: Request,
    gw: RuntimeGateway = Depends(get_gateway),
) -> EventSourceResponse:
    """SSE 流式聊天端点：走 Transport → Processor → SseEncoder 完整管线。"""

    transport: WebTransport = req.app.state.web_transport
    processor: ChannelProcessor = req.app.state.web_processor

    async def event_generator():
        logger.info("event_generator entered, session_id=%s", request.session_id)
        event = transport.receive(
            request.text,
            session_id=request.session_id,
            client_event_id=request.client_event_id,
        )
        try:
            async for chunk in processor.process_stream(event):
                yield SseEncoder.encode_chunk(chunk)
            logger.info("event_generator loop finished normally")
        except Exception as e:
            logger.warning("event_generator exception: %s", e)
            yield SseEncoder.encode_error(e)
        logger.info("event_generator yielding [DONE]")
        yield SseEncoder.encode_done()

    return EventSourceResponse(event_generator())
