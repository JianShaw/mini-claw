"""AgentRunService：定时任务专用的 Agent 执行服务。

直接调用 AgentRunner，绕过 Gateway。职责：
  1. 从 SessionStore 加载 session
  2. 注入 agent runtime profile（与 Gateway 保持一致）
  3. 检查并执行自动压缩（防止 session 历史无限增长）
  4. 调用 AgentRunner.run(session, message) — 复用 ContextBuildingAgentRunner
  5. 保存 session + 可选 memory 更新
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from claw.scheduler.types import AgentRun

if TYPE_CHECKING:
    from claw.ports import AgentRunner, ContextCompressor, SessionStore
    from claw.types import AgentReply, InboundMessage, Session

logger = logging.getLogger(__name__)


class AgentRunService:
    """定时任务专用的 Agent 执行服务：直接调用 AgentRunner，不走 Gateway。

    复用 ContextBuildingAgentRunner（Gateway 同款），自动获得 memory 注入、skills 注入。
    Gateway 处理外部通道消息（用户输入），AgentRunService 处理内部调度执行。
    """

    def __init__(
        self,
        agent_runner: AgentRunner,
        session_store: SessionStore,
        memory_manager: Any | None = None,
        *,
        compressor: ContextCompressor | None = None,
        agent_resolver: Any | None = None,
    ) -> None:
        self._agent_runner = agent_runner
        self._session_store = session_store
        self._memory_manager = memory_manager
        self._compressor = compressor
        self._agent_resolver = agent_resolver

    async def execute(self, run: AgentRun) -> AgentReply:
        """执行 AgentRun：加载 session → 注入 profile → 压缩 → 调用 runner → 保存。"""
        session = await self._resolve_session(run)

        # 注入 agent runtime profile（与 Gateway 保持一致）
        await self._inject_agent_runtime_profile(session)

        # 自动压缩检查（防止长时间定时任务的 session 历史无限增长）
        await self._auto_compress_if_needed(session, run.prompt)

        msg = self._build_inbound_message(run)
        reply = await self._agent_runner.run(session, msg)
        await self._deliver(session, reply, run)
        return reply

    # ------------------------------------------------------------------
    # Session 解析
    # ------------------------------------------------------------------

    async def _resolve_session(self, run: AgentRun) -> Session:
        """从 SessionStore 按 session_id 加载目标 session。"""
        session = await self._session_store.get_by_id(run.session_id)
        if session is None:
            raise ValueError(f"Session not found: {run.session_id}")
        return session

    # ------------------------------------------------------------------
    # Agent runtime profile 注入（与 Gateway._inject_agent_runtime_profile 对齐）
    # ------------------------------------------------------------------

    async def _inject_agent_runtime_profile(self, session: Session) -> None:
        """按 session.agent_id 解析运行配置并注入 session metadata。"""
        if self._agent_resolver is None:
            session.metadata.pop("agent_runtime_profile", None)
            return
        profile = self._agent_resolver.resolve(session.agent_id)
        from dataclasses import asdict
        session.metadata["agent_runtime_profile"] = asdict(profile)

    # ------------------------------------------------------------------
    # 自动压缩（与 Gateway._auto_compress_if_needed 对齐）
    # ------------------------------------------------------------------

    async def _auto_compress_if_needed(
        self, session: Session, incoming_text: str
    ) -> None:
        """检查并执行自动压缩，防止 session 历史无限增长。"""
        if self._compressor is None:
            return
        if not self._compressor.should_compress(session, incoming_text=incoming_text):
            return
        summary = await self._compressor.compress(session)
        if summary is not None:
            await self._session_store.save(session)
            logger.info(
                "Auto-compressed session %s for scheduled task",
                session.session_id,
            )

    # ------------------------------------------------------------------
    # 消息适配：AgentRun → InboundMessage
    # ------------------------------------------------------------------

    def _build_inbound_message(self, run: AgentRun) -> InboundMessage:
        """将内部 AgentRun 转换为 AgentRunner 所需的 InboundMessage。"""
        from claw.types import InboundMessage

        parts = run.peer_key.split(":", 2)
        metadata: dict[str, Any] = {
            "scheduled": True,
            "task_name": run.task_name,
        }

        return InboundMessage(
            channel=parts[0] if len(parts) > 0 else "local",
            account_id=parts[1] if len(parts) > 1 else "app",
            peer_id=parts[2] if len(parts) > 2 else "user",
            sender_id="scheduler",
            message_id=f"sched-{run.task_name}-{int(time.time() * 1000)}",
            text=run.prompt,
            timestamp=int(time.time() * 1000),
            message_type="text",
            raw=None,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # DeliveryRouter：结果投递
    # ------------------------------------------------------------------

    async def _deliver(
        self, session: Session, reply: AgentReply, run: AgentRun
    ) -> None:
        """保存 session + 可选 memory 更新。"""
        await self._session_store.save(session)
        logger.info(
            "AgentRun delivered: task=%s session=%s reply_len=%d",
            run.task_name, run.session_id, len(reply.text or ""),
        )

        if self._memory_manager is not None:
            try:
                await self._memory_manager.maybe_update_daily(session)
            except Exception:
                logger.debug(
                    "Failed to update memory for session %s",
                    run.session_id, exc_info=True,
                )
