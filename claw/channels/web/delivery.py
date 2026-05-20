"""Web 投递层：StreamChunk → SSE event 编码。"""

from __future__ import annotations

import json

from claw.types import StreamChunk


class SseEncoder:
    """将 StreamChunk 编码为 SSE 事件字典，供 EventSourceResponse 使用。"""

    @staticmethod
    def encode_chunk(chunk: StreamChunk) -> dict[str, str]:
        data = json.dumps(
            {"type": chunk.type, "text": chunk.text}, ensure_ascii=False
        )
        return {"data": data}

    @staticmethod
    def encode_error(error: Exception) -> dict[str, str]:
        data = json.dumps({"type": "error", "text": str(error)}, ensure_ascii=False)
        return {"data": data}

    @staticmethod
    def encode_done() -> dict[str, str]:
        return {"data": "[DONE]"}
