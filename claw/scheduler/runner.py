"""TaskRunner：任务执行器，将 TaskDefinition 转化为执行动作。

替代旧 executor.py 的三个平铺函数，提供清晰的分层：
  - LLM 路径：resolve_config → AgentRun → AgentRunService.execute
  - 系统路径：handler(context, **params)
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from claw.scheduler.history import TaskRunHistory
from claw.scheduler.types import (
    AgentRun,
    TaskDefinition,
    TaskResult,
    TaskRunRecord,
)

if TYPE_CHECKING:
    from claw.scheduler.agent_run import AgentRunService
    from claw.scheduler.context import TaskContext

logger = logging.getLogger(__name__)


class TaskRunner:
    """任务执行器：从队列消费任务，路由到 LLM 或系统执行路径。

    职责边界：
      - TaskConfigResolver（_resolve_config）：从 TaskDefinition 提取执行配置
      - LLM 路径：构造 AgentRun → 委托 AgentRunService
      - 系统路径：直接调用 handler(context, **params)
      - 统一记录执行历史
    """

    def __init__(
        self,
        agent_run_service: AgentRunService,
        context: TaskContext | None,
        handlers: dict[str, Callable[..., Awaitable[TaskResult]] | None],
        history: TaskRunHistory,
    ) -> None:
        self._agent_run_service = agent_run_service
        self._context = context
        self._handlers = handlers
        self._history = history

    async def run(self, name: str, definition: TaskDefinition) -> TaskResult:
        """统一入口：路由到 LLM 或系统路径，记录执行历史。"""
        triggered_at = datetime.now().isoformat()

        if definition.is_llm_task:
            result = await self._run_llm_task(name, definition)
            task_type = "llm"
        else:
            result = await self._run_system_task(name, definition)
            task_type = "system"

        self._record_history(name, triggered_at, task_type, result)
        return result

    # ------------------------------------------------------------------
    # LLM 路径：TaskConfigResolver → AgentRunService
    # ------------------------------------------------------------------

    def _resolve_config(self, name: str, definition: TaskDefinition) -> AgentRun:
        """TaskConfigResolver：从 TaskDefinition 提取执行配置构建 AgentRun。"""
        return AgentRun(
            session_id=definition.params.get("session_id", ""),
            agent_id=definition.params.get("agent_id", ""),
            peer_key=definition.peer_key or "",
            prompt=definition.prompt or "",
            task_name=name,
        )

    async def _run_llm_task(
        self, name: str, definition: TaskDefinition
    ) -> TaskResult:
        """LLM 任务：resolve_config → AgentRunService.execute → TaskResult。"""
        run = self._resolve_config(name, definition)

        if not run.peer_key or not run.prompt:
            return TaskResult(
                task_name=name, success=False,
                error="Missing peer_key or prompt for LLM task",
            )

        logger.info("LLM task '%s' dispatching via AgentRunService", name)
        try:
            reply = await self._agent_run_service.execute(run)
            logger.info(
                "LLM task '%s' completed: %s",
                name, (reply.text or "")[:120],
            )
            return TaskResult(
                task_name=name, success=True,
                message=f"Delivered: {(reply.text or '')[:200]}",
            )
        except Exception as exc:
            logger.exception("LLM task '%s' failed", name)
            return TaskResult(task_name=name, success=False, error=str(exc))

    # ------------------------------------------------------------------
    # 系统路径：直接调用 handler
    # ------------------------------------------------------------------

    async def _run_system_task(
        self, name: str, definition: TaskDefinition
    ) -> TaskResult:
        """系统任务：直接调用 handler(context, **params)。"""
        handler = self._handlers.get(name)
        if handler is None or self._context is None:
            return TaskResult(
                task_name=name, success=False,
                error="No handler or context for system task",
            )

        logger.info("System task '%s' executing", name)
        try:
            result = await handler(self._context, **definition.params)
            if not isinstance(result, TaskResult):
                result = TaskResult(task_name=name, success=True, message=str(result))
            logger.info(
                "System task '%s' completed: success=%s, message=%s",
                name, result.success, (result.message or result.error or "")[:120],
            )
            return result
        except Exception as exc:
            logger.exception("System task '%s' failed", name)
            return TaskResult(task_name=name, success=False, error=str(exc))

    # ------------------------------------------------------------------
    # 历史记录
    # ------------------------------------------------------------------

    def _record_history(
        self,
        name: str,
        triggered_at: str,
        task_type: str,
        result: TaskResult,
    ) -> None:
        """记录执行历史（best-effort，不影响主流程）。"""
        try:
            self._history.record(TaskRunRecord(
                task_name=name,
                triggered_at=triggered_at,
                completed_at=datetime.now().isoformat(),
                success=result.success,
                task_type=task_type,
                message=result.message[:500] if result.message else "",
                error=result.error,
            ))
        except Exception:
            logger.debug("Failed to record run history for '%s'", name, exc_info=True)
