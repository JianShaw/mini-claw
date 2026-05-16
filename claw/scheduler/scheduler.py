"""TaskScheduler：纯 asyncio 定时任务调度器。

两种执行模式：
- LLM 任务（peer_key + prompt）：触发时构建 InboundMessage → gateway 全链路
- 系统任务（handler）：触发时直接调用 handler（用于内存维护、compact 等）
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from claw.scheduler.history import TaskRunHistory
from claw.scheduler.types import (
    CronTrigger,
    EventTrigger,
    IntervalTrigger,
    TaskDefinition,
    TaskResult,
    TaskRunRecord,
)

if True:  # 避免循环导入
    from claw.gateway import RuntimeGateway
    from claw.scheduler.context import TaskContext
    from claw.types import InboundMessage

logger = logging.getLogger(__name__)


def _import_callable(dotted_path: str) -> Callable[..., Awaitable[TaskResult]]:
    """根据 dotted path 导入异步可调用对象。"""
    module_path, _, func_name = dotted_path.rpartition(".")
    module = importlib.import_module(module_path)
    func = getattr(module, func_name)
    return func


def _seconds_until_next_cron(expression: str) -> float:
    """计算距下一个 cron 匹配时间的秒数。"""
    parts = expression.strip().split()
    now = datetime.now()
    candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    # 最多扫描 366 天
    for _ in range(525960):
        if _cron_matches(parts, candidate):
            return (candidate - now).total_seconds()
        candidate += timedelta(minutes=1)
    return 86400.0


def _cron_field_matches(field: str, value: int) -> bool:
    """判断 cron 单个字段是否匹配给定值。"""
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return value % step == 0
    # 逗号分隔
    for item in field.split(","):
        if "-" in item:
            lo, hi = item.split("-", 1)
            if int(lo) <= value <= int(hi):
                return True
        else:
            if value == int(item):
                return True
    return False


def _cron_matches(parts: list[str], dt: datetime) -> bool:
    """判断 datetime 是否匹配 5 字段 cron 表达式。"""
    values = [dt.minute, dt.hour, dt.day, dt.month, dt.weekday()]
    return all(_cron_field_matches(p, v) for p, v in zip(parts, values))


class TaskScheduler:
    """定时任务调度器，使用 asyncio 原语实现。

    生命周期：
        scheduler = TaskScheduler(gateway=gateway, context=task_context)
        scheduler.register(TaskDefinition(...))
        await scheduler.start()
        ...
        await scheduler.stop()

    LLM 任务（peer_key + prompt）：
        触发 → 构建 InboundMessage → gateway.handle_inbound_message
        → session → AgentRunner → Delivery → 结果自然进入 session history

    系统任务（handler）：
        触发 → 直接调用 handler(task_context, **params)
        → TaskResult → 记录到 run history
    """

    def __init__(
        self,
        gateway: RuntimeGateway,
        context: TaskContext | None = None,
        history: TaskRunHistory | None = None,
    ) -> None:
        self._gateway = gateway
        self._context = context
        self._history = history or TaskRunHistory()
        self._tasks: dict[str, TaskDefinition] = {}
        self._handlers: dict[str, Callable[..., Awaitable[TaskResult]] | None] = {}
        self._asyncio_tasks: dict[str, asyncio.Task] = {}
        self._last_result: dict[str, TaskResult] = {}
        self._running = False
        self._stop_event = asyncio.Event()
        self._events: dict[str, asyncio.Event] = {}

    def register(self, definition: TaskDefinition) -> None:
        """注册任务定义，必须在 start() 之前调用。"""
        if definition.name in self._tasks:
            raise ValueError(f"Task already registered: {definition.name}")
        self._tasks[definition.name] = definition

        if definition.is_llm_task:
            # LLM 任务：不需要 handler，通过 gateway 路由
            self._handlers[definition.name] = None
        else:
            # 系统任务：解析 handler
            handler = definition.handler
            if isinstance(handler, str):
                handler = _import_callable(handler)
            self._handlers[definition.name] = handler

        # 预创建 event-driven 任务所需的 asyncio.Event
        if isinstance(definition.trigger, EventTrigger):
            if definition.trigger.event_name not in self._events:
                self._events[definition.trigger.event_name] = asyncio.Event()
        if self._running and definition.enabled:
            task = asyncio.create_task(
                self._run_loop(definition.name), name=f"scheduler-{definition.name}"
            )
            self._asyncio_tasks[definition.name] = task

    async def unregister(self, name: str) -> None:
        """移除任务并取消其运行中的循环。"""
        task = self._asyncio_tasks.pop(name, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.pop(name, None)
        self._handlers.pop(name, None)
        self._last_result.pop(name, None)

    async def upsert(self, definition: TaskDefinition) -> None:
        """注册任务，替换同名已有任务。"""
        if definition.name in self._tasks:
            await self.unregister(definition.name)
        self.register(definition)

    async def start(self) -> None:
        """启动所有已注册且 enabled 的任务。"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        enabled = [(n, d) for n, d in self._tasks.items() if d.enabled]
        for name, definition in enabled:
            task_type = "llm" if definition.is_llm_task else "system"
            logger.info(
                "Scheduler registering task '%s' [%s, %s]",
                name, task_type, type(definition.trigger).__name__,
            )
            asyncio_task = asyncio.create_task(
                self._run_loop(name), name=f"scheduler-{name}"
            )
            self._asyncio_tasks[name] = asyncio_task
        logger.info("Scheduler starting, %d task(s)", len(enabled))

    async def stop(self) -> None:
        """取消所有运行中的任务并等待结束。"""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        for task in self._asyncio_tasks.values():
            task.cancel()
        for task in self._asyncio_tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._asyncio_tasks.clear()

    async def emit(self, event_name: str, **payload: Any) -> None:
        """发射命名事件，携带 payload 供 task 读取。"""
        if self._context is not None:
            if event_name not in self._context._event_payloads:
                self._context._event_payloads[event_name] = []
            self._context._event_payloads[event_name].append(payload)
            if len(self._context._event_payloads[event_name]) > 10:
                self._context._event_payloads[event_name] = self._context._event_payloads[event_name][-10:]
        event = self._events.get(event_name)
        if event is not None:
            event.set()

    async def run_now(self, name: str) -> TaskResult:
        """手动触发指定任务，不论触发类型。"""
        definition = self._tasks.get(name)
        if definition is None:
            return TaskResult(task_name=name, success=False, error="Task not found")
        return await self._execute_by_name(name)

    def list_tasks(self) -> list[dict[str, Any]]:
        """返回所有注册任务的状态列表，供 /tasks 显示。"""
        result = []
        for name, definition in self._tasks.items():
            last = self._last_result.get(name)
            result.append({
                "name": name,
                "enabled": definition.enabled,
                "description": definition.description,
                "trigger_type": type(definition.trigger).__name__,
                "task_type": "llm" if definition.is_llm_task else "system",
                "last_result": last,
            })
        return result

    # --- 内部循环分发 ---

    async def _run_loop(self, name: str) -> None:
        """根据 trigger 类型运行对应的循环。"""
        definition = self._tasks[name]
        trigger = definition.trigger
        try:
            if isinstance(trigger, IntervalTrigger):
                await self._interval_loop(name, trigger)
            elif isinstance(trigger, CronTrigger):
                await self._cron_loop(name, trigger)
            elif isinstance(trigger, EventTrigger):
                await self._event_loop(name, trigger)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Task '%s' loop crashed", name)

    async def _interval_loop(self, name: str, trigger: IntervalTrigger) -> None:
        """每隔 trigger.seconds 执行一次。"""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=trigger.seconds
                )
                break
            except asyncio.TimeoutError:
                pass
            await self._execute_by_name(name)

    async def _cron_loop(self, name: str, trigger: CronTrigger) -> None:
        """在 cron 匹配时间执行。"""
        while not self._stop_event.is_set():
            delay = _seconds_until_next_cron(trigger.expression)
            if delay <= 0:
                delay = 60
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=delay
                )
                break
            except asyncio.TimeoutError:
                pass
            await self._execute_by_name(name)

    async def _event_loop(self, name: str, trigger: EventTrigger) -> None:
        """收到事件时重置计时器，超时无事件则触发 task。"""
        event = self._events.get(trigger.event_name)
        if event is None:
            return
        timeout = trigger.idle_timeout_seconds
        while not self._stop_event.is_set():
            event.clear()
            try:
                if timeout is not None:
                    await asyncio.wait_for(event.wait(), timeout=timeout)
                    continue
                else:
                    await event.wait()
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                break
            await self._execute_by_name(name)

    # --- 执行调度 ---

    async def _execute_by_name(self, name: str) -> TaskResult:
        """根据任务类型选择执行路径，记录到 run history。"""
        definition = self._tasks[name]
        triggered_at = datetime.now().isoformat()

        if definition.is_llm_task:
            result = await self._trigger_llm_task(name)
            task_type = "llm"
        else:
            result = await self._execute_handler(name)
            task_type = "system"

        self._last_result[name] = result

        # 记录执行历史
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

        return result

    async def _trigger_llm_task(self, name: str) -> TaskResult:
        """LLM 任务：构建 InboundMessage，走 gateway 全链路。"""
        definition = self._tasks[name]
        peer_key = definition.peer_key
        prompt = definition.prompt or ""

        if not peer_key or not prompt:
            return TaskResult(
                task_name=name, success=False,
                error="Missing peer_key or prompt for LLM task",
            )

        parts = peer_key.split(":", 2)
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
            metadata={"scheduled": True, "task_name": name},
        )

        logger.info("LLM task '%s' dispatching to %s", name, peer_key)
        try:
            reply = await self._gateway.handle_inbound_message(msg)
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

    async def _execute_handler(self, name: str) -> TaskResult:
        """系统任务：直接调用 handler。"""
        definition = self._tasks[name]
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
