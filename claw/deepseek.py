"""DeepSeek Agent 运行器：通过 OpenAI 兼容接口调用 DeepSeek 模型，支持思考模式和工具调用。"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from time import time
from typing import Any

from openai import AsyncOpenAI

from claw.types import AgentReply, ChatMessage, InboundMessage, Session, StreamChunk

if True:  # 避免循环导入
    from claw.tools import ToolsRegistry

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

_TOOL_LIMIT_PROMPT = (
    "工具调用次数已达上限。请根据目前已有的工具调用结果，"
    "总结你目前发现的信息，并给出你能给出的最佳回答。"
    "不要尝试继续调用工具。"
)

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

    def _tool_extra_kwargs(self, session: Session) -> dict[str, Any]:
        """从 RuntimeProfile 提取运行时上下文，注入到工具调用参数中。"""
        profile = session.metadata.get("agent_runtime_profile")
        if not profile:
            return {}
        sandbox_root = profile.get("sandbox_root", "")
        if not sandbox_root:
            return {}
        return {"_sandbox_root": sandbox_root}

    def _has_tools(self) -> bool:
        """检查是否有可用的工具。"""
        return self._tools_registry is not None and bool(self._tools_registry.list())

    def _build_kwargs(self, messages: list[dict[str, Any]], session: Session | None = None) -> dict[str, Any]:
        """构建 API 调用参数，有工具时自动附加 tools 参数。

        session 不为 None 时，从 RuntimeProfile 的 model_config 覆盖本轮 model/temperature。
        """
        # 从 RuntimeProfile 解析本轮 model 配置
        model = self.model
        temperature = None
        if session is not None:
            profile = session.metadata.get("agent_runtime_profile")
            if profile and profile.get("model_config"):
                mc = profile["model_config"]
                if "name" in mc:
                    model = mc["name"]
                if "temperature" in mc:
                    temperature = mc["temperature"]

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "extra_body": {"thinking": self._thinking_options()},
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if self._has_tools():
            # 按 Agent 配置过滤本轮可见工具
            filter_kwargs: dict[str, Any] = {}
            if session is not None:
                profile = session.metadata.get("agent_runtime_profile")
                if profile:
                    filter_kwargs["enabled_tools"] = profile["enabled_tools"]
                    filter_kwargs["enabled_mcp_servers"] = profile["enabled_mcp_servers"]
            tools_schema = self._tools_registry.to_openai_tools(**filter_kwargs)  # type: ignore[union-attr]
            if tools_schema:
                kwargs["tools"] = tools_schema
        return kwargs

    async def _finalize_tool_limit(
        self, session: Session, messages: list[dict[str, Any]],
    ) -> str:
        """工具迭代耗尽时，注入提示让 LLM 做最终总结。

        不带 tools 调用 LLM，避免继续工具调用。API 失败时 fallback 到静态文本。
        """
        messages.append({"role": "user", "content": _TOOL_LIMIT_PROMPT})
        session.history.append(ChatMessage(role="user", content=_TOOL_LIMIT_PROMPT, ts=int(time() * 1000)))

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "extra_body": {"thinking": self._thinking_options()},
        }

        try:
            response = await self.client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error("Finalize tool limit API error: %s", e)
            fallback = "抱歉，工具调用次数已达上限，无法继续操作。"
            session.history.append(ChatMessage(role="assistant", content=fallback, ts=int(time() * 1000)))
            return fallback

        text = response.choices[0].message.content or ""
        session.history.append(ChatMessage(role="assistant", content=text, ts=int(time() * 1000)))
        return text

    def _build_messages(self, session: Session) -> list[dict[str, Any]]:
        """构建发给 LLM 的 messages 列表，正确处理工具调用和工具结果消息。"""
        messages: list[dict[str, Any]] = []
        # Agent system prompt（最高优先级，来自 RuntimeProfile）
        profile = session.metadata.get("agent_runtime_profile")
        if profile and profile.get("system_prompt"):
            messages.append({"role": "system", "content": profile["system_prompt"]})
        if session.summary:
            messages.append({
                "role": "system",
                "content": f"以下是之前对话的摘要：\n{session.summary}",
            })
        memory_context = session.metadata.get("memory_context")
        if memory_context:
            # 记忆上下文由 Gateway/MemoryManager 准备，这里作为独立 system
            # message 注入；优先级说明写在 memory_context 模板里。
            messages.append({
                "role": "system",
                "content": str(memory_context),
            })
        # 技能信息注入：轻量级索引（Layer 1），供 LLM 浏览可用技能
        # 完整指令（Layer 2）由 LLM 通过 load_skill 工具按需加载
        skills_listing = session.metadata.get("skills_listing")
        if skills_listing:
            messages.append({
                "role": "system",
                "content": str(skills_listing),
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
        session.history.append(ChatMessage(role="user", content=message.text, ts=int(time() * 1000)))
        messages = self._build_messages(session)
        kwargs = self._build_kwargs(messages, session)
        tool_ctx = self._tool_extra_kwargs(session)

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
                session.history.append(ChatMessage(role="assistant", content=text, ts=int(time() * 1000)))
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
            session.history.append(ChatMessage(role="assistant", content="", tool_calls=tool_calls_data, reasoning_content=reasoning, ts=int(time() * 1000)))
            api_assistant_msg: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": tool_calls_data}
            if reasoning:
                api_assistant_msg["reasoning_content"] = reasoning
            messages.append(api_assistant_msg)

            # 逐个执行工具调用
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                    result = await self._tools_registry.execute(tc.function.name, args, **tool_ctx)  # type: ignore[union-attr]
                    result_str = str(result)
                except Exception as e:
                    result_str = f"Tool error: {type(e).__name__}: {e}"
                    logger.warning("Tool %s execution failed: %s", tc.function.name, e)

                session.history.append(ChatMessage(
                    role="tool", content=result_str,
                    tool_call_id=tc.id, tool_name=tc.function.name,
                    ts=int(time() * 1000),
                ))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})

            iterations += 1
            kwargs = self._build_kwargs(messages, session)

        # 超过最大迭代次数 → 让 LLM 做最终总结
        logger.warning("Tool iteration limit (%d) reached", self._max_tool_iterations)
        final_text = await self._finalize_tool_limit(session, messages)
        return AgentReply(
            text=final_text,
            metadata={"tool_iterations_exhausted": True},
        )

    async def run_stream(self, session: Session, message: InboundMessage) -> AsyncIterator[StreamChunk]:
        """流式版本：通过 stream=True 调用 API，逐 chunk yield StreamChunk。
        支持工具调用：当 LLM 请求工具调用时，执行工具后重新调用 API 继续流式输出。"""
        session.history.append(ChatMessage(role="user", content=message.text, ts=int(time() * 1000)))
        messages = self._build_messages(session)
        tool_ctx = self._tool_extra_kwargs(session)

        iterations = 0
        while iterations < self._max_tool_iterations:
            kwargs = self._build_kwargs(messages, session)
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

            # 通知调用方即将执行的工具名称
            tool_names = ", ".join(tc["function"]["name"] for tc in tool_calls_list)
            yield StreamChunk(
                type="tool_call",
                text=tool_names,
            )

            # 记录 assistant 的工具调用消息（保存 reasoning_content 以便后续恢复）
            session.history.append(ChatMessage(role="assistant", content="", tool_calls=tool_calls_list, reasoning_content=full_reasoning or None, ts=int(time() * 1000)))
            api_assistant_msg: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": tool_calls_list}
            if full_reasoning:
                api_assistant_msg["reasoning_content"] = full_reasoning
            messages.append(api_assistant_msg)

            # 执行每个工具
            for tc in tool_calls_list:
                try:
                    args = json.loads(tc["function"]["arguments"])
                    result = await self._tools_registry.execute(tc["function"]["name"], args, **tool_ctx)  # type: ignore[union-attr]
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
                    ts=int(time() * 1000),
                ))
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_str})

            iterations += 1

        # 超过最大迭代次数 → 让 LLM 做最终总结
        logger.warning("Tool iteration limit (%d) reached", self._max_tool_iterations)
        final_text = await self._finalize_tool_limit(session, messages)
        yield StreamChunk(type="content", text=final_text)
