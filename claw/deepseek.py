"""DeepSeek Agent 运行器：通过 OpenAI 兼容接口调用 DeepSeek 模型，支持思考模式。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from claw.types import AgentReply, ChatMessage, InboundMessage, Session, StreamChunk

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class DeepSeekAgentRunner:
    """通过 DeepSeek API 生成回复的 Agent 运行器。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        thinking: bool | None = None,
    ) -> None:
        key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        url = base_url or os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
        self.client = AsyncOpenAI(api_key=key, base_url=url)
        self.model = model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)
        self.thinking = thinking if thinking is not None else os.environ.get("DEEPSEEK_THINKING", "").lower() in ("true", "1", "yes")

    async def run(self, session: Session, message: InboundMessage) -> AgentReply:
        session.history.append(ChatMessage(role="user", content=message.text))

        messages = self._build_messages(session)

        kwargs: dict = {"model": self.model, "messages": messages}
        if self.thinking:
            kwargs["reasoning_effort"] = "high"

        response = await self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        text = msg.content or ""

        reasoning = getattr(msg, "reasoning_content", None) if self.thinking else None

        session.history.append(ChatMessage(role="assistant", content=text))
        metadata: dict[str, Any] = {}
        if reasoning:
            metadata["reasoning"] = reasoning
        return AgentReply(text=text, metadata=metadata)

    def _build_messages(self, session: Session) -> list[dict[str, str]]:
        """构建发给 LLM 的 messages 列表，有 summary 时在开头插入 system message。"""
        messages: list[dict[str, str]] = []
        if session.summary:
            messages.append({
                "role": "system",
                "content": f"以下是之前对话的摘要：\n{session.summary}",
            })
        messages.extend({"role": m.role, "content": m.content} for m in session.history)
        return messages

    async def run_stream(self, session: Session, message: InboundMessage) -> AsyncIterator[StreamChunk]:
        """流式版本：通过 stream=True 调用 API，逐 chunk yield StreamChunk。
        thinking 模式下先产出 thinking chunk，再产出 content chunk。assistant history 由调用方追加。"""
        session.history.append(ChatMessage(role="user", content=message.text))
        messages = self._build_messages(session)

        kwargs: dict = {"model": self.model, "messages": messages, "stream": True}
        if self.thinking:
            kwargs["reasoning_effort"] = "high"

        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            delta = chunk.choices[0].delta
            # 思考内容（thinking 模式下先于 content 出现）
            reasoning = getattr(delta, "reasoning_content", None) or ""
            if reasoning:
                yield StreamChunk(type="thinking", text=reasoning)
            # 正文内容
            content = delta.content or ""
            if content:
                yield StreamChunk(type="content", text=content)
