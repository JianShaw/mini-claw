# 定时任务调度系统

> **Status**: DONE
> **Created**: 2026-05-15
> **Reviewed**: 2026-05-15 — 修正事件语义、TaskContext 契约、Cron 校验

## Context

mini-claw 需要支持三种定时任务模式：Cron 式定时触发、间隔轮询、事件驱动（如空闲超时），用于自动执行 memory distill、session compact、MCP 健康检查等后台任务。使用纯 asyncio 实现，不引入外部依赖。

## 职责边界

核心设计原则：**调度器只管"何时"，不管"对谁"和"做什么"**。

```
┌─────────────────────────────────────────────────────────────┐
│  Scheduler（通用运行器）                                      │
│  职责：何时触发、错误隔离、生命周期                             │
│  不知道：session、peer、memory、gateway 等领域概念             │
│  handler 签名：(ctx: TaskContext, **params) -> TaskResult    │
└──────────────────────┬──────────────────────────────────────┘
                       │ TaskContext（边界对象）
┌──────────────────────┼──────────────────────────────────────┐
│  TaskContext（领域访问入口）                                   │
│  职责：为 task 提供发现和操作目标的 typed 接口                   │
│  - all_peers() → list[str]          所有 peer_key             │
│  - active_session(peer) → Session | None                     │
│  - memory_manager → MemoryManager                            │
│  - compact(peer) → str | None                                │
│  - distill() → int                                           │
│  - emit(event_name, **payload) → None                        │
│  - last_event_payload(event, key) → str | None               │
└──────────────────────┼──────────────────────────────────────┘
                       │ 具体 task 实现
┌──────────────────────┼──────────────────────────────────────┐
│  Tasks（业务逻辑）                                            │
│  职责：决定对谁做什么                                         │
│  - global scope task：遍历 all_peers()，逐个操作              │
│  - event scope task：从 last_event_payload 获取具体 peer      │
│  - 无 active session 时返回 skipped，不隐式创建新 session     │
└─────────────────────────────────────────────────────────────┘
```

**为什么不用 flat dict context：**
- `dict[str, Any]` 让 task 和内部实现耦合（直接取 `session_store._index`？）
- peer_key 硬编码在 task 里，多 peer 时需要 task 自己遍历 index
- TaskContext 封装了"怎么找到目标"的逻辑，task 只写"对目标做什么"

## 新增文件

| 文件 | 职责 |
|------|------|
| `claw/scheduler/__init__.py` | 公共 API 导出 |
| `claw/scheduler/types.py` | `CronTrigger`, `IntervalTrigger`, `EventTrigger`, `TaskDefinition`, `TaskResult` 数据类 |
| `claw/scheduler/context.py` | `TaskContext`：领域访问入口，封装 session/memory/gateway 操作 |
| `claw/scheduler/scheduler.py` | `TaskScheduler` 核心：start/stop/register/emit/run_now/list_tasks |
| `claw/scheduler/tasks.py` | 预定义任务：`daily_memory_distill`, `periodic_memory_update`, `idle_auto_compact` |
| `claw/scheduler/config.py` | `ScheduleConfigLoader`：从 JSON 加载任务配置，含 cron 语法校验 |
| `schedule_config.json` | 默认任务配置文件 |
| `tests/test_scheduler.py` | 调度器核心测试 |
| `tests/test_scheduler_tasks.py` | 预定义任务测试 |
| `tests/test_scheduler_config.py` | 配置加载测试 |

## 修改文件

| 文件 | 改动 |
|------|------|
| `claw/agent.py` | 新增 `schedule_config_path` 参数，start/stop 管理调度器生命周期，新增 `get_task_status()`/`run_task()`/`emit_event()` |
| `chat/app.py` | 新增 `/tasks` 和 `/task run <name>` 命令，每条消息后 emit `session_activity` 事件（携带 peer_key） |

## 核心设计

### TaskContext (`claw/scheduler/context.py`)

```python
@dataclass(slots=True)
class TaskContext:
    """调度器和 task 之间的边界对象。

    调度器只把 TaskContext 传给 handler，不传裸 store/manager。
    TaskContext 封装了"如何找到目标 session/peer"的查询逻辑，
    task 只需要调用方法，不需要知道底层的 peer_key 拼接规则、
    session store 的 index 结构等实现细节。

    关键约束：无 active session 时返回 None / skipped，
    绝不隐式创建新 session。
    """
    _session_store: SessionStore
    _memory_manager: MemoryManager | None
    _gateway: RuntimeGateway | None
    _event_payloads: dict[str, list[dict[str, Any]]]

    # --- peer 发现 ---
    def all_peers(self) -> list[str]:
        """返回所有有活跃 session 的 peer_key 列表。"""

    # --- session 操作 ---
    async def active_session(self, peer_key: str) -> Session | None:
        """获取指定 peer 的活跃 session。无活跃 session 返回 None。"""

    async def all_active_sessions(self) -> list[tuple[str, Session]]:
        """获取所有 peer 的活跃 session，返回 (peer_key, Session) 列表。"""

    # --- memory 操作 ---
    @property
    def memory_manager(self) -> MemoryManager | None:
        """直接访问 memory manager（用于 distill 等不依赖 session 的操作）。"""

    async def update_daily(self, session: Session, *, force: bool = False) -> bool:
        """更新指定 session 的 daily memory。"""

    async def distill(self) -> int:
        """蒸馏长期记忆，返回新增条数。"""

    # --- session 压缩 ---
    async def compact(self, peer_key: str) -> str | None:
        """压缩指定 peer 的活跃 session，返回 summary 或 None。
        内部构造 routing message 定位 peer（gateway.compact_session 需要）。
        无活跃 session 时返回 None，不创建新 session。"""

    # --- event payload ---
    def last_event_payload(self, event_name: str, *, key: str) -> str | None:
        """获取指定事件最后一次 emit 携带的某个 key 的值。"""
```

**关键设计决策：**
- `compact(peer_key)` 内部构造 `InboundMessage` 路由到 gateway，解决了 gateway.compact_session 需要 routing message 的约束（参考 `claw/gateway.py:179`）
- `active_session(peer_key)` 返回 `Session | None`，task 必须处理 None 情况（返回 skipped）
- `all_active_sessions()` 返回 `(peer_key, Session)` 元组，global task 不需要自己拼 peer_key

### 事件语义：`session_activity` 而非 `session_idle`

**问题**：原方案用 `emit("session_idle")` 重置空闲计时器，名字暗示"session 空闲了"，但实际是"session 有活动"。容易实现成"收到消息就触发 idle 任务"。

**修正**：
- CLI 层每条消息后 `emit("session_activity", peer_key=current_peer_key)`
- 调度器的 `EventTrigger(event_name="session_activity", idle_timeout_seconds=600)` 语义变为：
  - 每次收到 `session_activity` 事件，重置 idle 计时器
  - 超过 600s 没有 `session_activity` → 触发 `idle_auto_compact`
- TaskTrigger 配置中 `idle_timeout_seconds` 明确表示"多长时间没有该事件时触发"

```python
# scheduler.py 中的 event loop 实现
async def _event_loop(self, name: str, trigger: EventTrigger) -> None:
    event = self._events.get(trigger.event_name)
    if event is None:
        return
    while not self._stop_event.is_set():
        event.clear()
        try:
            await asyncio.wait_for(event.wait(), timeout=trigger.idle_timeout_seconds)
            # 事件被 emit（有活动），重置计时器，不触发 task
            continue
        except asyncio.TimeoutError:
            # 超时无活动，触发 task
            pass
        await self._execute_by_name(name)
```

### Trigger 类型 (`claw/scheduler/types.py`)

```python
@dataclass(slots=True)
class CronTrigger:
    expression: str  # "0 9 * * *" = 每天9点

@dataclass(slots=True)
class IntervalTrigger:
    seconds: int     # 1800 = 每30分钟

@dataclass(slots=True)
class EventTrigger:
    event_name: str
    idle_timeout_seconds: float | None = None  # 无活动N秒后触发
```

### TaskScheduler (`claw/scheduler/scheduler.py`)

调度器是**纯粹的通用运行器**，不包含任何领域逻辑：

- handler 签名：`(ctx: TaskContext, **params) -> TaskResult`
- 每个 task 在独立 `asyncio.Task` 中运行
- Interval: `asyncio.wait_for(stop_event, timeout=seconds)` 循环
- Cron: 计算 `_seconds_until_next_cron()` 再 sleep
- Event: 收到事件重置计时器，超时触发 task
- `emit(event_name, **payload)` — payload 存入 context 供 task 读取
- 错误隔离：单个 task 异常不影响其他 task
- 停止：`stop_event.set()` + `task.cancel()` 双保险

### 预定义任务 (`claw/scheduler/tasks.py`)

| 任务 | Scope | 触发方式 | 功能 |
|------|-------|---------|------|
| `daily_memory_distill` | Global | Cron `"0 3 * * *"` | 凌晨3点蒸馏长期记忆（不依赖具体 session） |
| `periodic_memory_update` | Global | Interval 1800s | 每30分钟遍历所有活跃 session，更新 daily memory |
| `idle_auto_compact` | Per-peer | Event `session_activity` (idle 600s) | 指定 peer 空闲10分钟，compact + distill |

**示例 task 实现（展示 scope 差异和 skipped 处理）：**

```python
async def daily_memory_distill(ctx: TaskContext) -> TaskResult:
    """全局 task：distill 不依赖 session，直接操作 memory manager。"""
    if ctx.memory_manager is None:
        return TaskResult("daily_memory_distill", False, error="No memory_manager")
    result = await ctx.distill()
    return TaskResult("daily_memory_distill", True,
                      message=f"Distilled {result} item(s)")

async def periodic_memory_update(ctx: TaskContext) -> TaskResult:
    """全局 task：遍历所有活跃 session，更新各自的 daily memory。"""
    sessions = await ctx.all_active_sessions()
    if not sessions:
        return TaskResult("periodic_memory_update", True,
                          message="Skipped: no active sessions")
    updated = 0
    for peer_key, session in sessions:
        if await ctx.update_daily(session, force=True):
            updated += 1
    return TaskResult("periodic_memory_update", True,
                      message=f"Updated {updated}/{len(sessions)} session(s)")

async def idle_auto_compact(ctx: TaskContext) -> TaskResult:
    """Per-peer task：从 event payload 获取空闲的 peer_key。"""
    peer_key = ctx.last_event_payload("session_activity", key="peer_key")
    if not peer_key:
        return TaskResult("idle_auto_compact", True,
                          message="Skipped: no peer from event")
    session = await ctx.active_session(peer_key)
    if session is None:
        return TaskResult("idle_auto_compact", True,
                          message="Skipped: no active session")
    summary = await ctx.compact(peer_key)
    if summary is None:
        return TaskResult("idle_auto_compact", True, message="Nothing to compact")
    await ctx.distill()
    return TaskResult("idle_auto_compact", True,
                      message=f"Compacted {peer_key}: {summary[:80]}...")
```

### 配置格式 (`schedule_config.json`)

```json
{
  "tasks": {
    "daily_distill": {
      "trigger": { "type": "cron", "expression": "0 3 * * *" },
      "handler": "claw.scheduler.tasks.daily_memory_distill",
      "enabled": true,
      "description": "凌晨3点蒸馏长期记忆"
    },
    "periodic_memory": {
      "trigger": { "type": "interval", "seconds": 1800 },
      "handler": "claw.scheduler.tasks.periodic_memory_update",
      "enabled": true,
      "description": "每30分钟更新所有活跃session的daily memory"
    },
    "idle_compact": {
      "trigger": {
        "type": "event",
        "event_name": "session_activity",
        "idle_timeout_seconds": 600
      },
      "handler": "claw.scheduler.tasks.idle_auto_compact",
      "enabled": true,
      "description": "空闲10分钟自动compact"
    }
  }
}
```

### Cron 校验：配置加载时 fail fast

在 `ScheduleConfigLoader._parse_trigger()` 中，遇到 CronTrigger 时立即校验：
- 必须是 5 个字段
- 每个字段只允许 `*`、`*/N`、逗号分隔数字、单个数字
- 字段范围：minute 0-59, hour 0-23, day 1-31, month 1-12, weekday 0-6
- 不支持的语法（如 `L`, `W`, `#`）直接 raise `ValueError`，配置加载失败记录 warning 并跳过该 task

### MiniClaw 集成 (`claw/agent.py`)

- `__init__` 新增 `schedule_config_path` 参数
- `start()` 构造 `TaskContext`（注入 session_store, memory_manager, gateway），加载配置，注册任务，启动调度器
- `stop()` 停止调度器
- `emit_event(name, **payload)` — CLI 层调用，携带 peer_key 等上下文

### CLI 命令 (`chat/app.py`)

- `/tasks` — 列出所有任务及最近执行结果
- `/task run <name>` — 手动触发指定任务
- 每条用户消息处理后：`await claw.emit_event("session_activity", peer_key=current_peer_key)` 重置空闲计时器

## 测试覆盖

- **调度器核心**：注册/启停、interval 循环、cron 循环、event 触发（activity 重置 idle）、idle 超时触发、手动触发、错误隔离、context 传递
- **TaskContext**：all_peers、active_session（None 返回 skipped）、compact（无 session 返回 None）、distill
- **预定义任务**：global scope（遍历多 peer）、per-peer scope（无 session 时 skipped）的正常/异常路径
- **配置加载**：正常 JSON、缺失文件、非法 JSON、无效 trigger、cron fail fast
- **集成**：MiniClaw start/stop 带调度器、CLI 命令输出

## 验证

1. `uv run pytest tests/test_scheduler*.py -v` — 新模块测试
2. `uv run pytest tests/ -v` — 全量回归
3. 启动 chat app，运行 `/tasks` 确认任务列表，`/task run daily_distill` 手动触发
