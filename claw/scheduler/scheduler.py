"""TaskScheduler：单 dispatcher + worker pool 的 asyncio 定时任务调度器。

架构：
  - 一个 _dispatcher_task 负责计算到期任务并放入队列（CronScheduler）
  - max_workers 个 _worker_loop 从队列取出任务执行（TaskQueue）
  - _active_jobs 防止同一任务并发执行
  - _wake_event 用于中断 dispatcher 的睡眠

执行委托给 TaskRunner：
  - LLM 任务：TaskConfigResolver → AgentRun → AgentRunService.execute
  - 系统任务：直接调用 handler（用于内存维护、compact 等）
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from claw.scheduler.cron import import_callable, seconds_until_next_cron
from claw.scheduler.history import TaskRunHistory
from claw.scheduler.runner import TaskRunner
from claw.scheduler.types import (
    CronTrigger,
    EventTrigger,
    IntervalTrigger,
    TaskDefinition,
    TaskResult,
)

if True:  # 避免循环导入
    from claw.scheduler.agent_run import AgentRunService
    from claw.scheduler.context import TaskContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal dataclasses
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _ScheduleState:
    """单个任务的调度运行时状态。"""
    next_run_at: float | None
    generation: int
    enabled: bool


@dataclass(slots=True)
class _QueuedRun:
    """放入内部队列的待执行任务。"""
    name: str
    generation: int


# ---------------------------------------------------------------------------
# TaskScheduler
# ---------------------------------------------------------------------------

class TaskScheduler:
    """定时任务调度器，使用 dispatcher + worker pool 架构。

    生命周期：
        scheduler = TaskScheduler(gateway=gateway, context=task_context)
        scheduler.register(TaskDefinition(...))
        await scheduler.start()
        ...
        await scheduler.stop()
    """

    def __init__(
        self,
        agent_run_service: AgentRunService,
        context: TaskContext | None = None,
        history: TaskRunHistory | None = None,
        max_workers: int = 1,
    ) -> None:
        if max_workers < 1:
            raise ValueError(f"max_workers must be >= 1, got {max_workers}")
        self._context = context
        self._history = history or TaskRunHistory()
        self._max_workers = max_workers

        # 任务定义和处理器
        self._tasks: dict[str, TaskDefinition] = {}
        self._handlers: dict[str, Callable[..., Awaitable[TaskResult]] | None] = {}
        self._last_result: dict[str, TaskResult] = {}
        self._events: dict[str, asyncio.Event] = {}

        # TaskRunner：消费队列，执行 LLM / 系统任务
        self._runner = TaskRunner(
            agent_run_service=agent_run_service,
            context=self._context,
            handlers=self._handlers,
            history=self._history,
        )

        # Dispatcher + worker pool 运行时状态
        self._running = False
        self._stop_event = asyncio.Event()
        self._dispatcher_task: asyncio.Task | None = None
        self._worker_tasks: set[asyncio.Task] = set()
        self._queue: asyncio.Queue[_QueuedRun] | None = None
        self._wake_event: asyncio.Event | None = None
        self._active_jobs: set[str] = set()
        self._schedule_state: dict[str, _ScheduleState] = {}
        self._generation: int = 0

        # 保留旧字段以兼容外部引用（如测试中的 _asyncio_tasks）
        self._asyncio_tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # 时间原语
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def _compute_next_run(self, definition: TaskDefinition) -> float | None:
        """计算任务下一次调度时间（monotonic 时间戳）。"""
        now = self._now()
        trigger = definition.trigger
        if isinstance(trigger, IntervalTrigger):
            return now + trigger.seconds
        if isinstance(trigger, CronTrigger):
            return now + seconds_until_next_cron(trigger.expression)
        if isinstance(trigger, EventTrigger):
            if trigger.idle_timeout_seconds is not None:
                return now + trigger.idle_timeout_seconds
            return None
        return None

    def _set_next_run(self, name: str, next_run_at: float | None) -> None:
        """更新任务的 next_run_at 并唤醒 dispatcher。"""
        state = self._schedule_state.get(name)
        if state is not None:
            state.next_run_at = next_run_at
        if self._wake_event is not None:
            self._wake_event.set()

    # ------------------------------------------------------------------
    # 注册 / 反注册
    # ------------------------------------------------------------------

    def register(self, definition: TaskDefinition) -> None:
        """注册任务定义。"""
        if definition.name in self._tasks:
            raise ValueError(f"Task already registered: {definition.name}")
        self._tasks[definition.name] = definition

        if definition.is_llm_task:
            self._handlers[definition.name] = None
        else:
            handler = definition.handler
            if isinstance(handler, str):
                handler = import_callable(handler)
            self._handlers[definition.name] = handler

        # 预创建 event-driven 任务所需的 asyncio.Event
        if isinstance(definition.trigger, EventTrigger):
            if definition.trigger.event_name not in self._events:
                self._events[definition.trigger.event_name] = asyncio.Event()

        # 运行时注册：初始化调度状态并唤醒 dispatcher
        if self._running and definition.enabled:
            self._generation += 1
            self._schedule_state[definition.name] = _ScheduleState(
                next_run_at=self._compute_next_run(definition),
                generation=self._generation,
                enabled=True,
            )
            if self._wake_event is not None:
                self._wake_event.set()

    async def unregister(self, name: str) -> None:
        """移除任务，不中断正在执行的作业。"""
        self._tasks.pop(name, None)
        self._handlers.pop(name, None)
        self._last_result.pop(name, None)
        self._schedule_state.pop(name, None)

    async def upsert(self, definition: TaskDefinition) -> None:
        """注册任务，替换同名已有任务。"""
        if definition.name in self._tasks:
            await self.unregister(definition.name)
        self.register(definition)

    # ------------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动 dispatcher 和 worker pool。"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()

        self._queue = asyncio.Queue()
        self._active_jobs = set()
        self._wake_event = asyncio.Event()
        self._worker_tasks = set()

        # 初始化已注册且 enabled 的任务的调度状态
        enabled_count = 0
        for name, definition in self._tasks.items():
            if not definition.enabled:
                continue
            enabled_count += 1
            task_type = "llm" if definition.is_llm_task else "system"
            logger.info(
                "Scheduler registering task '%s' [%s, %s]",
                name, task_type, type(definition.trigger).__name__,
            )
            self._generation += 1
            self._schedule_state[name] = _ScheduleState(
                next_run_at=self._compute_next_run(definition),
                generation=self._generation,
                enabled=True,
            )

        # 创建 dispatcher
        self._dispatcher_task = asyncio.create_task(
            self._dispatcher_loop(), name="scheduler-dispatcher"
        )

        # 创建 worker pool
        for i in range(self._max_workers):
            worker = asyncio.create_task(
                self._worker_loop(i), name=f"scheduler-worker-{i}"
            )
            self._worker_tasks.add(worker)

        logger.info(
            "Scheduler starting, %d task(s), %d worker(s)",
            enabled_count, self._max_workers,
        )

    async def stop(self) -> None:
        """取消 dispatcher 和所有 worker 并等待结束。"""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._wake_event is not None:
            self._wake_event.set()

        # 取消 dispatcher
        if self._dispatcher_task is not None:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
            self._dispatcher_task = None

        # 取消所有 worker
        for worker in self._worker_tasks:
            worker.cancel()
        for worker in self._worker_tasks:
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._worker_tasks.clear()

        self._queue = None
        self._wake_event = None
        self._active_jobs.clear()
        self._schedule_state.clear()
        self._asyncio_tasks.clear()

    # ------------------------------------------------------------------
    # 事件和手动触发
    # ------------------------------------------------------------------

    async def emit(self, event_name: str, **payload: Any) -> None:
        """发射命名事件，携带 payload 供 task 读取。"""
        if self._context is not None:
            if event_name not in self._context._event_payloads:
                self._context._event_payloads[event_name] = []
            self._context._event_payloads[event_name].append(payload)
            if len(self._context._event_payloads[event_name]) > 10:
                self._context._event_payloads[event_name] = self._context._event_payloads[event_name][-10:]

        # 同时设置旧风格的 asyncio.Event（兼容性）
        event = self._events.get(event_name)
        if event is not None:
            event.set()

        # 更新匹配 event trigger 的调度状态
        now = self._now()
        for name, definition in self._tasks.items():
            trigger = definition.trigger
            if not isinstance(trigger, EventTrigger):
                continue
            if trigger.event_name != event_name:
                continue
            state = self._schedule_state.get(name)
            if state is None or not state.enabled:
                continue
            if trigger.idle_timeout_seconds is not None:
                state.next_run_at = now + trigger.idle_timeout_seconds
            else:
                state.next_run_at = now

        if self._wake_event is not None:
            self._wake_event.set()

    async def run_now(self, name: str) -> TaskResult:
        """手动触发指定任务，不论触发类型。

        如果任务正在被调度执行（active），返回 busy 失败。
        """
        definition = self._tasks.get(name)
        if definition is None:
            return TaskResult(task_name=name, success=False, error="Task not found")
        if name in self._active_jobs:
            return TaskResult(task_name=name, success=False, error="Task already running")
        self._active_jobs.add(name)
        try:
            return await self._do_execute(name, definition)
        finally:
            self._active_jobs.discard(name)

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

    # ------------------------------------------------------------------
    # Dispatcher loop
    # ------------------------------------------------------------------

    async def _dispatcher_loop(self) -> None:
        """计算到期任务，放入队列，休眠直到下一个到期时间或被唤醒。"""
        while not self._stop_event.is_set():
            now = self._now()

            # 收集到期任务
            for name, state in list(self._schedule_state.items()):
                if state.next_run_at is None:
                    continue
                if state.next_run_at > now:
                    continue
                definition = self._tasks.get(name)
                if definition is None or not definition.enabled:
                    continue
                if not state.enabled:
                    continue
                if name in self._active_jobs:
                    continue

                state.next_run_at = None
                self._active_jobs.add(name)
                if self._queue is not None:
                    await self._queue.put(_QueuedRun(name=name, generation=state.generation))
                    logger.debug("Dispatcher enqueued task '%s' (gen=%d)", name, state.generation)

            # 计算最近的 next_run_at
            nearest: float | None = None
            for state in self._schedule_state.values():
                if state.next_run_at is None:
                    continue
                if nearest is None or state.next_run_at < nearest:
                    nearest = state.next_run_at

            delay: float | None = None
            if nearest is not None:
                delay = max(0.0, nearest - self._now())

            if self._stop_event.is_set():
                break

            if self._wake_event is not None:
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    async def _worker_loop(self, worker_id: int) -> None:
        """从队列取出任务执行。"""
        while not self._stop_event.is_set():
            if self._queue is None:
                await asyncio.sleep(0.05)
                continue
            try:
                queued = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            name = queued.name
            generation = queued.generation

            # 检查任务是否仍然存在且代际匹配
            definition = self._tasks.get(name)
            state = self._schedule_state.get(name)
            if definition is None or state is None or state.generation != generation:
                self._active_jobs.discard(name)
                logger.debug(
                    "Worker-%d skipped stale job '%s' (gen=%d)", worker_id, name, generation,
                )
                continue

            logger.debug("Worker-%d executing task '%s'", worker_id, name)
            try:
                result = await self._do_execute(name, definition)
                self._last_result[name] = result
            except Exception:
                logger.exception("Worker-%d task '%s' raised unhandled exception", worker_id, name)
            finally:
                self._active_jobs.discard(name)

                # 执行完毕后重新调度（如果任务仍存在且代际匹配）
                current_def = self._tasks.get(name)
                current_state = self._schedule_state.get(name)
                if current_def is not None and current_state is not None:
                    if current_state.generation == generation and current_def.enabled:
                        current_state.next_run_at = self._compute_next_run(current_def)
                        if self._wake_event is not None:
                            self._wake_event.set()

    # ------------------------------------------------------------------
    # 执行委托
    # ------------------------------------------------------------------

    async def _do_execute(self, name: str, definition: TaskDefinition) -> TaskResult:
        """委托 TaskRunner 执行任务。"""
        result = await self._runner.run(name, definition)
        self._last_result[name] = result
        return result
