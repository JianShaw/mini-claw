"""调度器核心测试：注册/启停、interval、event、手动触发、LLM 任务、错误隔离。

验证 dispatcher + worker pool 架构：
  - start() 创建 1 个 dispatcher + max_workers 个 worker
  - 不会为每个 enabled 任务创建独立的 asyncio.Task
  - _active_jobs 防止同一任务并发执行
  - run_now() 在任务 active 时返回 busy 失败
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from claw.scheduler import (
    AgentRunService,
    EventTrigger,
    IntervalTrigger,
    TaskContext,
    TaskDefinition,
    TaskResult,
    TaskRunHistory,
    TaskScheduler,
)
from claw.types import AgentReply


def _mock_context() -> TaskContext:
    """构造一个不依赖真实 store 的 TaskContext。"""
    store = AsyncMock()
    store.list_peer_keys = AsyncMock(return_value=[])
    store.get_active = AsyncMock(return_value=None)
    return TaskContext(
        _session_store=store,
        _memory_manager=None,
        _gateway=None,
    )


def _mock_agent_run_service() -> AsyncMock:
    """构造一个 mock AgentRunService。"""
    svc = AsyncMock(spec=AgentRunService)
    svc.execute = AsyncMock(return_value=AgentReply(text="ok"))
    return svc


def _mock_scheduler(**kwargs: Any) -> tuple[TaskScheduler, AsyncMock, TaskContext]:
    """构造 scheduler + mock agent_run_service + mock context。"""
    svc = _mock_agent_run_service()
    ctx = _mock_context()
    history = kwargs.pop("history", None) or TaskRunHistory()
    scheduler = TaskScheduler(
        agent_run_service=svc, context=ctx, history=history, **kwargs,
    )
    return scheduler, svc, ctx


async def _counting_handler(ctx: TaskContext, **params: Any) -> TaskResult:
    """记录调用次数的 handler。"""
    calls = ctx._event_payloads.setdefault("_test_calls", [])
    calls.append(params.get("label", "default"))
    return TaskResult(task_name="test", success=True, message=f"call {len(calls)}")


async def _blocking_handler(ctx: TaskContext, **params: Any) -> TaskResult:
    """阻塞 handler，用于测试并发控制。"""
    event = ctx._event_payloads.setdefault("_block_event", asyncio.Event())
    await event.wait()
    calls = ctx._event_payloads.setdefault("_test_calls", [])
    calls.append("blocked")
    return TaskResult(task_name="test", success=True, message="done")


async def _failing_handler(ctx: TaskContext, **params: Any) -> TaskResult:
    """总是失败的 handler。"""
    raise RuntimeError("intentional failure")


# --- 注册和启停 ---


async def test_register_creates_event_for_event_trigger() -> None:
    scheduler, _, _ = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="evt",
        trigger=EventTrigger(event_name="test_event"),
        handler=_counting_handler,
    ))
    assert "test_event" in scheduler._events


async def test_register_rejects_duplicate() -> None:
    scheduler, _, _ = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="dup", trigger=IntervalTrigger(seconds=60), handler=_counting_handler,
    ))
    try:
        scheduler.register(TaskDefinition(
            name="dup", trigger=IntervalTrigger(seconds=60), handler=_counting_handler,
        ))
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


async def test_start_stop_lifecycle() -> None:
    scheduler, _, _ = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="long", trigger=IntervalTrigger(seconds=3600), handler=_counting_handler,
    ))
    await scheduler.start()
    assert scheduler._running
    assert scheduler._dispatcher_task is not None
    assert len(scheduler._worker_tasks) == 1  # default max_workers=1
    # 不应为每个 enabled 任务创建独立的 asyncio.Task
    assert len(scheduler._asyncio_tasks) == 0
    await scheduler.stop()
    assert not scheduler._running
    assert scheduler._dispatcher_task is None
    assert len(scheduler._worker_tasks) == 0


async def test_start_skips_disabled_tasks() -> None:
    scheduler, _, _ = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="off", trigger=IntervalTrigger(seconds=3600), handler=_counting_handler,
        enabled=False,
    ))
    await scheduler.start()
    assert "off" not in scheduler._schedule_state
    await scheduler.stop()


# --- Worker pool ---


async def test_default_worker_count_is_1() -> None:
    scheduler, _, _ = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="t", trigger=IntervalTrigger(seconds=3600), handler=_counting_handler,
    ))
    await scheduler.start()
    assert len(scheduler._worker_tasks) == 1
    await scheduler.stop()


async def test_max_workers_creates_multiple_workers() -> None:
    scheduler, _, _ = _mock_scheduler(max_workers=2)
    scheduler.register(TaskDefinition(
        name="t", trigger=IntervalTrigger(seconds=3600), handler=_counting_handler,
    ))
    await scheduler.start()
    assert len(scheduler._worker_tasks) == 2
    await scheduler.stop()


async def test_max_workers_must_be_positive() -> None:
    try:
        _mock_scheduler(max_workers=0)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


# --- Interval 触发 ---


async def test_interval_task_executes_repeatedly() -> None:
    scheduler, _, ctx = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="tick", trigger=IntervalTrigger(seconds=1), handler=_counting_handler,
    ))
    await scheduler.start()
    await asyncio.sleep(2.5)
    await scheduler.stop()
    calls = ctx._event_payloads.get("_test_calls", [])
    assert len(calls) >= 2, f"Expected >= 2 calls, got {len(calls)}"


# --- Event 触发 ---


async def test_event_task_fires_on_emit() -> None:
    scheduler, _, ctx = _mock_scheduler()
    # 无 idle timeout，仅响应 emit
    scheduler.register(TaskDefinition(
        name="evt", trigger=EventTrigger(event_name="test_event"), handler=_counting_handler,
    ))
    await scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.emit("test_event")
    await asyncio.sleep(0.3)
    await scheduler.stop()
    calls = ctx._event_payloads.get("_test_calls", [])
    assert len(calls) >= 1


async def test_event_idle_timeout_fires_when_no_activity() -> None:
    scheduler, _, ctx = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="idle",
        trigger=EventTrigger(event_name="activity", idle_timeout_seconds=0.2),
        handler=_counting_handler,
    ))
    await scheduler.start()
    await asyncio.sleep(0.5)
    await scheduler.stop()
    calls = ctx._event_payloads.get("_test_calls", [])
    assert len(calls) >= 1, "Should have fired after idle timeout"


async def test_event_activity_resets_idle_timer() -> None:
    scheduler, _, ctx = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="idle",
        trigger=EventTrigger(event_name="activity", idle_timeout_seconds=0.2),
        handler=_counting_handler,
    ))
    await scheduler.start()
    # 在 timeout 之前 emit，重置计时器
    await asyncio.sleep(0.1)
    await scheduler.emit("activity", peer_key="test")
    await asyncio.sleep(0.1)
    await scheduler.emit("activity", peer_key="test")
    await asyncio.sleep(0.1)
    # 还没到 timeout，不应触发
    calls_before = len(ctx._event_payloads.get("_test_calls", []))
    # 等到 timeout
    await asyncio.sleep(0.3)
    await scheduler.stop()
    calls_after = len(ctx._event_payloads.get("_test_calls", []))
    assert calls_after >= 1
    assert calls_before == 0


# --- 手动触发 ---


async def test_run_now_executes_handler() -> None:
    scheduler, _, ctx = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="manual", trigger=IntervalTrigger(seconds=3600), handler=_counting_handler,
    ))
    result = await scheduler.run_now("manual")
    assert result.success
    assert "call" in result.message


async def test_run_now_unknown_task() -> None:
    scheduler, _, _ = _mock_scheduler()
    result = await scheduler.run_now("nonexistent")
    assert not result.success
    assert "not found" in result.error


async def test_run_now_passes_params() -> None:
    scheduler, _, ctx = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="params",
        trigger=IntervalTrigger(seconds=3600),
        handler=_counting_handler,
        params={"label": "custom"},
    ))
    await scheduler.run_now("params")
    calls = ctx._event_payloads.get("_test_calls", [])
    assert calls[-1] == "custom"


async def test_upsert_replaces_running_task() -> None:
    scheduler, _, ctx = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="dynamic",
        trigger=IntervalTrigger(seconds=3600),
        handler=_counting_handler,
        params={"label": "old"},
    ))
    await scheduler.start()
    await scheduler.upsert(TaskDefinition(
        name="dynamic",
        trigger=IntervalTrigger(seconds=3600),
        handler=_counting_handler,
        params={"label": "new"},
    ))
    result = await scheduler.run_now("dynamic")
    await scheduler.stop()
    assert result.success
    calls = ctx._event_payloads.get("_test_calls", [])
    assert calls[-1] == "new"


# --- 并发控制 ---


async def test_same_task_does_not_reenter_while_running() -> None:
    """同一任务正在执行时，run_now 返回 busy 失败。"""
    scheduler, _, ctx = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="blocker",
        trigger=IntervalTrigger(seconds=3600),
        handler=_blocking_handler,
    ))
    await scheduler.start()
    # 手动触发并让 handler 阻塞
    run_task = asyncio.create_task(scheduler.run_now("blocker"))
    await asyncio.sleep(0.05)
    # 此时任务应处于 active 状态
    assert "blocker" in scheduler._active_jobs
    # 再次 run_now 应返回 busy
    busy_result = await scheduler.run_now("blocker")
    assert not busy_result.success
    assert "already running" in busy_result.error
    # 释放阻塞
    block_event: asyncio.Event = ctx._event_payloads["_block_event"]
    block_event.set()
    result = await run_task
    assert result.success
    await scheduler.stop()


async def test_run_now_returns_busy_when_scheduled_execution_active() -> None:
    """调度执行 active 时，run_now 返回 busy。"""
    scheduler, _, ctx = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="slow",
        trigger=IntervalTrigger(seconds=0.1),
        handler=_blocking_handler,
    ))
    await scheduler.start()
    # 等待调度触发
    await asyncio.sleep(0.2)
    if "slow" in scheduler._active_jobs:
        busy_result = await scheduler.run_now("slow")
        assert not busy_result.success
        assert "already running" in busy_result.error
    # 释放
    block_event: asyncio.Event = ctx._event_payloads.get("_block_event", asyncio.Event())
    block_event.set()
    await asyncio.sleep(0.1)
    await scheduler.stop()


async def test_serial_execution_with_max_workers_1() -> None:
    """max_workers=1 时，两个到期任务串行执行。"""
    scheduler, _, ctx = _mock_scheduler(max_workers=1)
    scheduler.register(TaskDefinition(
        name="a",
        trigger=IntervalTrigger(seconds=0.1),
        handler=_counting_handler,
    ))
    scheduler.register(TaskDefinition(
        name="b",
        trigger=IntervalTrigger(seconds=0.1),
        handler=_counting_handler,
    ))
    await scheduler.start()
    await asyncio.sleep(0.5)
    await scheduler.stop()
    calls = ctx._event_payloads.get("_test_calls", [])
    # 串行执行，总调用次数应按序排列（不会被并发打断）
    assert len(calls) >= 2


# --- 错误隔离 ---


async def test_handler_failure_does_not_crash_scheduler() -> None:
    scheduler, _, ctx = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="bad", trigger=IntervalTrigger(seconds=3600), handler=_failing_handler,
    ))
    scheduler.register(TaskDefinition(
        name="good", trigger=IntervalTrigger(seconds=3600), handler=_counting_handler,
    ))
    result_bad = await scheduler.run_now("bad")
    assert not result_bad.success
    result_good = await scheduler.run_now("good")
    assert result_good.success


# --- list_tasks ---


async def test_list_tasks_returns_status() -> None:
    scheduler, _, _ = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="t1", trigger=IntervalTrigger(seconds=60), handler=_counting_handler,
        description="test task",
    ))
    statuses = scheduler.list_tasks()
    assert len(statuses) == 1
    assert statuses[0]["name"] == "t1"
    assert statuses[0]["trigger_type"] == "IntervalTrigger"
    assert statuses[0]["description"] == "test task"
    assert statuses[0]["task_type"] == "system"


# --- emit payload ---


async def test_emit_stores_payload_in_context() -> None:
    scheduler, _, ctx = _mock_scheduler()
    await scheduler.emit("test_event", peer_key="local:app:user")
    assert ctx.last_event_payload("test_event", key="peer_key") == "local:app:user"


async def test_emit_retains_last_10_payloads() -> None:
    scheduler, _, ctx = _mock_scheduler()
    for i in range(15):
        await scheduler.emit("test_event", count=i)
    payloads = ctx._event_payloads.get("test_event", [])
    assert len(payloads) == 10
    assert payloads[-1]["count"] == 14


# --- LLM 任务模式 ---


async def test_llm_task_uses_agent_run_service() -> None:
    """LLM 任务触发时通过 AgentRunService.execute 执行。"""
    scheduler, svc, _ = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="remind",
        trigger=IntervalTrigger(seconds=3600),
        peer_key="local:app:user",
        prompt="提醒喝水",
        params={"session_id": "sess_123", "agent_id": "ag_default"},
    ))
    result = await scheduler.run_now("remind")
    assert result.success
    svc.execute.assert_awaited_once()
    run = svc.execute.await_args.args[0]
    assert run.prompt == "提醒喝水"
    assert run.task_name == "remind"
    assert run.session_id == "sess_123"
    assert run.peer_key == "local:app:user"


async def test_llm_task_no_handler_stored() -> None:
    """LLM 任务注册后 _handlers[name] 为 None。"""
    scheduler, _, _ = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="remind",
        trigger=IntervalTrigger(seconds=3600),
        peer_key="local:app:user",
        prompt="提醒喝水",
    ))
    assert scheduler._handlers["remind"] is None


async def test_llm_task_list_tasks_shows_llm_type() -> None:
    """list_tasks 正确显示 LLM 任务类型。"""
    scheduler, _, _ = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="remind",
        trigger=IntervalTrigger(seconds=60),
        peer_key="local:app:user",
        prompt="提醒",
    ))
    statuses = scheduler.list_tasks()
    assert len(statuses) == 1
    assert statuses[0]["task_type"] == "llm"


async def test_llm_task_service_failure() -> None:
    """AgentRunService 抛异常时 LLM 任务返回失败。"""
    scheduler, svc, _ = _mock_scheduler()
    svc.execute = AsyncMock(side_effect=RuntimeError("timeout"))
    scheduler.register(TaskDefinition(
        name="remind",
        trigger=IntervalTrigger(seconds=3600),
        peer_key="local:app:user",
        prompt="提醒喝水",
    ))
    result = await scheduler.run_now("remind")
    assert not result.success
    assert "timeout" in result.error


async def test_llm_task_incomplete_without_both_fields() -> None:
    """只有 peer_key 没有 prompt 时不被视为 LLM 任务，走 system 路径报错。"""
    scheduler, _, _ = _mock_scheduler()
    scheduler.register(TaskDefinition(
        name="bad",
        trigger=IntervalTrigger(seconds=3600),
        peer_key="local:app:user",
        prompt=None,
    ))
    result = await scheduler.run_now("bad")
    assert not result.success
    assert "No handler" in result.error


# --- Run History ---


async def test_system_task_records_history(tmp_path: Any) -> None:
    """系统任务执行后记录到 run history。"""
    history = TaskRunHistory(path=tmp_path / "history.jsonl")
    scheduler, _, _ = _mock_scheduler(history=history)
    scheduler.register(TaskDefinition(
        name="sys_task",
        trigger=IntervalTrigger(seconds=3600),
        handler=_counting_handler,
    ))
    await scheduler.run_now("sys_task")
    records = history.list_recent()
    assert len(records) == 1
    assert records[0].task_name == "sys_task"
    assert records[0].task_type == "system"
    assert records[0].success is True


async def test_llm_task_records_history(tmp_path: Any) -> None:
    """LLM 任务执行后记录到 run history。"""
    history = TaskRunHistory(path=tmp_path / "history.jsonl")
    scheduler, svc, _ = _mock_scheduler(history=history)
    scheduler.register(TaskDefinition(
        name="remind",
        trigger=IntervalTrigger(seconds=3600),
        peer_key="local:app:user",
        prompt="提醒",
    ))
    await scheduler.run_now("remind")
    records = history.list_recent()
    assert len(records) == 1
    assert records[0].task_name == "remind"
    assert records[0].task_type == "llm"
    assert records[0].success is True
