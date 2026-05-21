"""任务执行逻辑：LLM 任务和系统任务的执行路径。"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from claw.scheduler.history import TaskRunHistory
from claw.scheduler.types import (
    TaskDefinition,
    TaskResult,
    TaskRunRecord,
)

if True:  # 避免循环导入
    from claw.gateway import RuntimeGateway
    from claw.scheduler.context import TaskContext
    from claw.types import InboundMessage

logger = logging.getLogger(__name__)


async def execute_task(
    name: str,
    definition: TaskDefinition,
    *,
    gateway: RuntimeGateway,
    context: TaskContext | None,
    handlers: dict[str, Callable[..., Awaitable[TaskResult]] | None],
    history: TaskRunHistory,
) -> TaskResult:
    """统一的任务执行入口，路由到 LLM 或系统任务，记录 run history。"""
    triggered_at = datetime.now().isoformat()

    if definition.is_llm_task:
        result = await trigger_llm_task(name, definition, gateway=gateway)
        task_type = "llm"
    else:
        result = await execute_handler(name, definition, context=context, handlers=handlers)
        task_type = "system"

    try:
        history.record(TaskRunRecord(
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

    return result


async def trigger_llm_task(
    name: str,
    definition: TaskDefinition,
    *,
    gateway: RuntimeGateway,
) -> TaskResult:
    """LLM 任务：构建 InboundMessage，走 gateway 全链路。"""
    peer_key = definition.peer_key
    prompt = definition.prompt or ""

    if not peer_key or not prompt:
        return TaskResult(
            task_name=name, success=False,
            error="Missing peer_key or prompt for LLM task",
        )

    parts = peer_key.split(":", 2)
    # 携带 session_id 以精确路由到定时推送 session，避免 gateway 创建新 session
    metadata: dict[str, Any] = {"scheduled": True, "task_name": name}
    session_id = definition.params.get("session_id")
    if session_id:
        metadata["session_id"] = session_id

    msg = InboundMessage(
        channel=parts[0] if len(parts) > 0 else "local",
        account_id=parts[1] if len(parts) > 1 else "app",
        peer_id=parts[2] if len(parts) > 2 else "user",
        sender_id="scheduler",
        message_id=f"sched-{name}-{int(time.time() * 1000)}",
        text=prompt,
        timestamp=int(time.time() * 1000),
        message_type="text",
        raw=None,
        metadata=metadata,
    )

    logger.info("LLM task '%s' dispatching to %s", name, peer_key)
    try:
        reply = await gateway.handle_inbound_message(msg)
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


async def execute_handler(
    name: str,
    definition: TaskDefinition,
    *,
    context: TaskContext | None,
    handlers: dict[str, Callable[..., Awaitable[TaskResult]] | None],
) -> TaskResult:
    """系统任务：直接调用 handler。"""
    handler = handlers.get(name)
    if handler is None or context is None:
        return TaskResult(
            task_name=name, success=False,
            error="No handler or context for system task",
        )

    logger.info("System task '%s' executing", name)
    try:
        result = await handler(context, **definition.params)
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
