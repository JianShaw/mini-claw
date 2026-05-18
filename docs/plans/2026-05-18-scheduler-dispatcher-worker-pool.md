# Scheduler Dispatcher Worker Pool Implementation Plan

> **For CC:** Execute this plan task-by-task. Do not implement watchdog, timeout abort, SQLite TaskRegistry, retry policy, or delivery idempotency in this pass.

**Goal:** Replace mini-claw's current one-background-task-per-scheduled-task model with a single scheduler dispatcher plus a fixed worker pool.

**Architecture:** `TaskScheduler` keeps the existing public task definitions and execution paths, but centralizes trigger calculation in one dispatcher loop. Due tasks are placed into an internal queue and executed by a bounded worker pool, with an `active_jobs` set preventing the same scheduled task from running concurrently.

**Tech Stack:** Python 3.11+, asyncio, existing `claw.scheduler` package, existing JSON schedule config and JSONL task history.

---

## Constraints And Non-Goals

- Keep `schedule_config.json` schema unchanged.
- Keep `TaskDefinition`, trigger dataclasses, LLM task behavior, system handler behavior, `/tasks`, and `/task run <name>` behavior compatible.
- Default worker concurrency is `1`; allow constructor override with `max_workers`.
- Do not add third-party dependencies such as `croniter`.
- Do not build watchdog, layered timeout handling, SQLite persistence, full task status state machine, retry/backoff, or idempotent delivery.

## Target Design

Current implementation in `claw/scheduler/scheduler.py` starts one `asyncio.Task` per enabled task via `_run_loop(name)`. Replace that with:

- one `_dispatcher_task`;
- `max_workers` worker tasks;
- one internal `asyncio.Queue`;
- one `_wake_event` to interrupt dispatcher sleeps;
- one `_active_jobs: set[str]` to prevent same-task reentry;
- per-task scheduling state containing `next_run_at`, `generation`, and whether the task is enabled.

Dispatcher responsibilities:

- compute `next_run_at` for enabled tasks on startup;
- find due tasks whose `next_run_at <= now`;
- skip disabled, missing, stale-generation, or active jobs;
- mark a due job active before enqueueing;
- sleep until the nearest `next_run_at` or until `_wake_event` is set.

Worker responsibilities:

- consume queued job requests;
- execute the existing `_execute_by_name(name)` path;
- remove `name` from `_active_jobs` in `finally`;
- recalculate that task's next schedule after completion if the definition still exists and the generation is current.

Event behavior:

- `emit()` continues to store the latest payloads in `TaskContext`.
- For `EventTrigger(..., idle_timeout_seconds=N)`, `emit()` resets `next_run_at = now + N`.
- For `EventTrigger(..., idle_timeout_seconds=None)`, `emit()` sets matching task `next_run_at = now` so it fires once.
- `emit()` always wakes the dispatcher after changing event-triggered schedules.

Manual run behavior:

- `run_now(name)` still executes immediately and returns `TaskResult`.
- If `name` is already in `_active_jobs`, return `TaskResult(task_name=name, success=False, error="Task already running")`.
- Manual execution should also use `_active_jobs` while running, so it cannot overlap with scheduled execution.

Dynamic task behavior:

- `register()` while stopped only stores definitions and handler state.
- `register()` while running initializes scheduling state and wakes the dispatcher; it must not create a per-task loop.
- `unregister()` removes definition, handler, last result, and scheduling state. If a stale queued request later reaches a worker, generation checks make it a no-op.
- `upsert()` remains unregister + register, and future executions must use the new definition.

## Implementation Tasks

### Task 1: Add Scheduler Runtime State

**Files:**
- Modify: `claw/scheduler/scheduler.py`

**Steps:**

1. Add a small private dataclass near `TaskScheduler`, for example `_ScheduleState`, with:
   - `next_run_at: float | None`
   - `generation: int`
   - `enabled: bool`
2. Add a small private dataclass for queued work, for example `_QueuedRun`, with:
   - `name: str`
   - `generation: int`
3. Update `TaskScheduler.__init__` to accept `max_workers: int = 1`.
4. Validate `max_workers >= 1`; raise `ValueError` otherwise.
5. Replace or stop using per-task `_asyncio_tasks` as the primary runtime model. Keep it only if needed for test compatibility, but prefer new fields:
   - `_dispatcher_task: asyncio.Task | None`
   - `_worker_tasks: set[asyncio.Task]`
   - `_queue: asyncio.Queue[_QueuedRun]`
   - `_wake_event: asyncio.Event`
   - `_active_jobs: set[str]`
   - `_schedule_state: dict[str, _ScheduleState]`
   - `_generation: int`

### Task 2: Add Trigger Scheduling Helpers

**Files:**
- Modify: `claw/scheduler/scheduler.py`

**Steps:**

1. Add `_now() -> float` returning `time.monotonic()`.
2. Keep existing cron expression matching helpers, but add a monotonic-compatible wrapper:
   - compute seconds until next cron using existing `_seconds_until_next_cron()`;
   - return `self._now() + delay`.
3. Add `_initial_next_run_at(definition) -> float | None`:
   - `IntervalTrigger`: `now + seconds`;
   - `CronTrigger`: `now + _seconds_until_next_cron(expression)`;
   - `EventTrigger` with idle timeout: `now + idle_timeout_seconds`;
   - `EventTrigger` without idle timeout: `None`.
4. Add `_next_after_completion(definition) -> float | None`:
   - same policy as initial scheduling, using completion time as the base for interval and idle triggers.
5. Add `_set_next_run(name, next_run_at)` that updates state and wakes dispatcher.

### Task 3: Replace Start And Stop Lifecycle

**Files:**
- Modify: `claw/scheduler/scheduler.py`
- Modify tests as needed: `tests/test_scheduler.py`

**Steps:**

1. Update `start()`:
   - return early if already running;
   - set `_running = True`, clear `_stop_event`, clear `_wake_event`;
   - initialize `_queue`, `_active_jobs`, and `_schedule_state` for enabled tasks;
   - create one `_dispatcher_task`;
   - create exactly `max_workers` worker tasks;
   - log enabled task count and worker count.
2. Update `stop()`:
   - set `_running = False`;
   - set `_stop_event` and `_wake_event`;
   - cancel dispatcher and workers;
   - await all of them, swallowing `asyncio.CancelledError`;
   - clear queue references, worker set, active jobs, and runtime state as appropriate.
3. Remove startup creation of one `_run_loop(name)` per task.

### Task 4: Implement Dispatcher And Workers

**Files:**
- Modify: `claw/scheduler/scheduler.py`

**Steps:**

1. Add `_dispatcher_loop()`:
   - while not stopped, collect due jobs from `_schedule_state`;
   - for each due job, skip if task missing, disabled, generation stale, or already active;
   - set state `next_run_at = None`;
   - add `name` to `_active_jobs`;
   - enqueue `_QueuedRun(name, generation)`;
   - calculate sleep delay until nearest remaining `next_run_at`;
   - wait on `_wake_event` with timeout equal to that delay;
   - clear `_wake_event` after wake.
2. Add `_worker_loop(worker_id: int)`:
   - read queued runs;
   - skip stale work if task no longer exists or generation differs;
   - execute `_execute_by_name(name)`;
   - in `finally`, discard from `_active_jobs`;
   - if current and still enabled, schedule next run via `_next_after_completion`.
3. Ensure a handler exception still becomes a failed `TaskResult` through existing `_execute_by_name` logic and does not crash the worker.

### Task 5: Update Register, Unregister, Upsert, Emit, Run Now

**Files:**
- Modify: `claw/scheduler/scheduler.py`

**Steps:**

1. In `register()`:
   - keep duplicate detection and handler import behavior;
   - keep event payload setup;
   - if running and enabled, create `_ScheduleState` with a new generation and initial `next_run_at`, then wake dispatcher.
2. In `unregister()`:
   - remove definitions and handlers as today;
   - increment or invalidate generation for that name;
   - remove scheduling state;
   - do not cancel currently executing work; allow it to finish, but prevent rescheduling.
3. In `upsert()`:
   - keep current external behavior, implemented as unregister then register.
4. In `emit()`:
   - preserve existing payload storage and last-10 truncation;
   - for every matching enabled `EventTrigger`, update schedule as described in Target Design;
   - wake dispatcher.
5. In `run_now()`:
   - return not found exactly as today for missing tasks;
   - if active, return busy failure;
   - otherwise mark active, execute `_execute_by_name`, and discard active in `finally`.

### Task 6: Update Tests

**Files:**
- Modify: `tests/test_scheduler.py`

**Steps:**

1. Update lifecycle tests so they assert:
   - `_dispatcher_task` exists after `start()`;
   - number of workers equals `max_workers`;
   - no per-task background task is created for every enabled task.
2. Keep existing interval, event, manual trigger, LLM, and history tests passing.
3. Add tests for:
   - default worker count is 1;
   - `max_workers=2` starts two workers;
   - two due tasks run serially when `max_workers=1`;
   - same task does not reenter while already running;
   - `run_now()` returns busy when scheduled execution is active;
   - event idle timer resets on repeated `emit()`;
   - non-idle event fires once per emit-driven schedule;
   - upsert while running uses new params on future execution.
4. Avoid brittle sleeps where possible by using short intervals, `asyncio.Event`, and handler-controlled blocking.

### Task 7: Verify

**Commands for CC to run after implementation:**

```powershell
uv run pytest tests/test_scheduler.py tests/test_scheduler_config.py tests/test_scheduler_history.py -v
uv run pytest tests/ -v
```

Expected result: scheduler tests pass, and full test suite has no regressions.

## Acceptance Criteria

- Starting 100 enabled scheduled tasks creates 1 dispatcher plus `max_workers` workers, not 100 task loops.
- With default settings, scheduled jobs execute one at a time.
- Different scheduled jobs can execute concurrently only when `max_workers > 1`.
- The same scheduled job never runs concurrently with itself.
- Dynamic task creation through `create_scheduled_task` still works.
- LLM scheduled tasks still flow through `gateway.handle_inbound_message`.
- System scheduled tasks still call their handler with `TaskContext`.
- Task run history remains JSONL and compatible with existing tests.
- No watchdog or timeout-abort code is added.
