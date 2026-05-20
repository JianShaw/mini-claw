"""运行时上下文构建器：在 AgentRunner 调用 LLM 前准备记忆和技能上下文。

从 Gateway 搬出，解决 Gateway 职责过重的问题。
Gateway 管"哪个 session、哪个 agent"，ContextBuilder 管"这个 agent 看到什么上下文"。
"""

from __future__ import annotations

from typing import Any

from claw.types import InboundMessage, Session


class RuntimeContextBuilder:
    """Agent 运行时上下文构建器：在 AgentRunner 调用 LLM 前准备记忆和技能上下文。

    不直接拼 LLM messages，而是把上下文写入 session.metadata；
    DeepSeekAgentRunner._build_messages() 读取 metadata 注入到提示词。
    """

    def __init__(
        self,
        *,
        memory_manager: Any | None = None,
        skills_registry: Any | None = None,
    ) -> None:
        self._memory_manager = memory_manager
        self._skills_registry = skills_registry

    async def build(self, session: Session, message: InboundMessage) -> None:
        """准备本轮运行时上下文，写入 session.metadata。"""
        await self._inject_memory_context(session, message)
        await self._inject_skill_context(session)

    async def _inject_memory_context(self, session: Session, message: InboundMessage) -> None:
        """将 memory_context 写入 session.metadata，供 runner 读取。"""
        if self._memory_manager is None:
            session.metadata.pop("memory_context", None)
            return
        context = await self._memory_manager.build_context(message)
        if context:
            session.metadata["memory_context"] = context
        else:
            session.metadata.pop("memory_context", None)

    async def _inject_skill_context(self, session: Session) -> None:
        """将技能轻量级索引写入 session.metadata，供 runner 读取。

        仅注入 skills_listing（Layer 1：name + description），
        完整指令（Layer 2）由 LLM 通过 load_skill 工具按需加载。
        有 RuntimeProfile 时按 enabled_skills 过滤。
        """
        registry = self._skills_registry
        if registry is None:
            session.metadata.pop("skills_listing", None)
            return

        # 按 Agent 配置过滤技能
        filter_kwargs: dict[str, Any] = {}
        profile = session.metadata.get("agent_runtime_profile")
        if profile and profile.get("enabled_skills") is not None:
            filter_kwargs["enabled_skills"] = profile["enabled_skills"]

        listing = registry.build_skills_listing(**filter_kwargs)
        if listing:
            session.metadata["skills_listing"] = listing
        else:
            session.metadata.pop("skills_listing", None)
