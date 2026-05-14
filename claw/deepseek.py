"""DeepSeek Agent 运行器：通过 OpenAI 兼容接口调用 DeepSeek 模型，支持思考模式和工具调用。"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from claw.types import AgentReply, ChatMessage, InboundMessage, Session, StreamChunk

if True:  # 避免循环导入
    from claw.tools import ToolsRegistry

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

logger = logging.getLogger(__name__)


class DeepSeekAgentRunner:
    """通过 DeepSeek API 生成回复的 Agent 运行器，支持工具调用。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        thinking: bool | None = None,
        tools_registry: ToolsRegistry | None = None,
        max_tool_iterations: int = 10,
    ) -> None:
        key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        url = base_url or os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
        self.client = AsyncOpenAI(api_key=key, base_url=url)
        self.model = model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)
        self.thinking = thinking if thinking is not None else os.environ.get("DEEPSEEK_THINKING", "").lower() in ("true", "1", "yes")
        self._tools_registry = tools_registry
        self._max_tool_iterations = max_tool_iterations

    def _thinking_options(self) -> dict[str, str]:
        return {
            "type": "enabled" if self.thinking else "disabled",
            "reasoning_effort": "high",
        }

    def _has_tools(self) -> bool:
        """检查是否有可用的工具。"""
        return self._tools_registry is not None and bool(self._tools_registry.list())

    def _build_kwargs(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """构建 API 调用参数，有工具时自动附加 tools 参数。"""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "extra_body": {"thinking": self._thinking_options()},
        }
        if self._has_tools():
            kwargs["tools"] = self._tools_registry.to_openai_tools()  # type: ignore[union-attr]
        return kwargs

    def _build_messages(self, session: Session) -> list[dict[str, Any]]:
        """构建发给 LLM 的 messages 列表，正确处理工具调用和工具结果消息。"""
        messages: list[dict[str, Any]] = []
        if session.summary:
            messages.append({
                "role": "system",
                "content": f"以下是之前对话的摘要：\n{session.summary}",
            })
        for m in session.history:
            if m.role == "tool":
                messages.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.content,
                })
            elif m.role == "assistant" and m.tool_calls:
                msg_dict: dict[str, Any] = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": m.tool_calls,
                }
                # thinking 模式：恢复历史中的 reasoning_content
                if m.reasoning_content:
                    msg_dict["reasoning_content"] = m.reasoning_content
                messages.append(msg_dict)
            else:
                messages.append({"role": m.role, "content": m.content})
        return messages

    async def run(self, session: Session, message: InboundMessage) -> AgentReply:
        """同步接口：调用 LLM 生成回复，支持工具调用循环。"""
        session.history.append(ChatMessage(role="user", content=message.text))
        messages = self._build_messages(session)
        kwargs = self._build_kwargs(messages)

        # 工具执行循环：LLM 返回 tool_calls 时执行工具，将结果送回 LLM
        iterations = 0
        while iterations < self._max_tool_iterations:
            try:
                response = await self.client.chat.completions.create(**kwargs)
            except Exception as e:
                logger.error("DeepSeek API error: %s", e)
                return AgentReply(text=f"[API error: {type(e).__name__}: {e}]")
            msg = response.choices[0].message

            # 无工具调用 → 提取文本返回
            if not msg.tool_calls:
                text = msg.content or ""
                reasoning = getattr(msg, "reasoning_content", None) if self.thinking else None
                session.history.append(ChatMessage(role="assistant", content=text))
                metadata: dict[str, Any] = {}
                if reasoning:
                    metadata["reasoning"] = reasoning
                return AgentReply(text=text, metadata=metadata)

            # 有工具调用 → 执行每个工具并收集结果
            tool_calls_data = [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
            # 思考模式：assistant 消息必须携带 reasoning_content 传回 API
            reasoning = getattr(msg, "reasoning_content", None) if self.thinking else None
            # 记录 assistant 的工具调用消息（保存 reasoning_content 以便后续恢复）
            session.history.append(ChatMessage(role="assistant", content="", tool_calls=tool_calls_data, reasoning_content=reasoning))
            api_assistant_msg: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": tool_calls_data}
            if reasoning:
                api_assistant_msg["reasoning_content"] = reasoning
            messages.append(api_assistant_msg)

            # 逐个执行工具调用
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                    result = await self._tools_registry.execute(tc.function.name, args)  # type: ignore[union-attr]
                    result_str = str(result)
                except Exception as e:
                    result_str = f"Tool error: {type(e).__name__}: {e}"
                    logger.warning("Tool %s execution failed: %s", tc.function.name, e)

                session.history.append(ChatMessage(
                    role="tool", content=result_str,
                    tool_call_id=tc.id, tool_name=tc.function.name,
                ))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})

            iterations += 1
            # 下一轮循环使用更新后的 messages
            kwargs = self._build_kwargs(messages)

        # 超过最大迭代次数
        logger.warning("Tool iteration limit (%d) reached", self._max_tool_iterations)
        return AgentReply(
            text="[tool call limit reached]",
            metadata={"tool_iterations_exhausted": True},
        )

    async def run_stream(self, session: Session, message: InboundMessage) -> AsyncIterator[StreamChunk]:
        """流式版本：通过 stream=True 调用 API，逐 chunk yield StreamChunk。
        支持工具调用：当 LLM 请求工具调用时，执行工具后重新调用 API 继续流式输出。"""
        session.history.append(ChatMessage(role="user", content=message.text))
        messages = self._build_messages(session)

        # 流式场景下可能需要多轮工具调用
        iterations = 0
        while iterations < self._max_tool_iterations:
            kwargs = self._build_kwargs(messages)
            kwargs["stream"] = True

            try:
                stream = await self.client.chat.completions.create(**kwargs)
            except Exception as e:
                logger.error("DeepSeek API stream error: %s", e)
                yield StreamChunk(type="content", text=f"[API error: {type(e).__name__}: {e}]")
                return

            # 收集流式 tool_calls delta 和思考内容
            tool_calls_accum: dict[int, dict[str, Any]] = {}
            full_reasoning = ""
            finish_reason = None

            async for chunk in stream:
                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason

                # 思考内容（thinking 模式下先于 content 出现）
                reasoning = getattr(delta, "reasoning_content", None) or ""
                if reasoning:
                    full_reasoning += reasoning
                    yield StreamChunk(type="thinking", text=reasoning)

                # 正文内容
                content = delta.content or ""
                if content:
                    yield StreamChunk(type="content", text=content)

                # 工具调用 delta
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_accum:
                            tool_calls_accum[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                        if tc_delta.id:
                            tool_calls_accum[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_accum[idx]["function"]["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_accum[idx]["function"]["arguments"] += tc_delta.function.arguments

            # 以 tool_calls_accum 为主判断是否有工具调用，finish_reason 作为辅助
            if not tool_calls_accum:
                return

            # 有工具调用 → 执行工具，通知调用方，然后继续循环
            tool_calls_list = [tool_calls_accum[i] for i in sorted(tool_calls_accum.keys())]

            yield StreamChunk(
                type="tool_call",
                text="",
            )

            # 记录 assistant 的工具调用消息（保存 reasoning_content 以便后续恢复）
            session.history.append(ChatMessage(role="assistant", content="", tool_calls=tool_calls_list, reasoning_content=full_reasoning or None))
            api_assistant_msg: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": tool_calls_list}
            if full_reasoning:
                api_assistant_msg["reasoning_content"] = full_reasoning
            messages.append(api_assistant_msg)

            # 执行每个工具
            for tc in tool_calls_list:
                try:
                    args = json.loads(tc["function"]["arguments"])
                    result = await self._tools_registry.execute(tc["function"]["name"], args)  # type: ignore[union-attr]
                    result_str = str(result)
                except Exception as e:
                    result_str = f"Tool error: {type(e).__name__}: {e}"
                    logger.warning("Tool %s execution failed: %s", tc["function"]["name"], e)

                yield StreamChunk(
                    type="tool_result",
                    text=result_str,
                )

                session.history.append(ChatMessage(
                    role="tool", content=result_str,
                    tool_call_id=tc["id"], tool_name=tc["function"]["name"],
                ))
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_str})

            iterations += 1

        # 超过最大迭代次数
        yield StreamChunk(type="system", text="[tool call limit reached]")
