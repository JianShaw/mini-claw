# OpenClaw 定时任务系统深度解析

> **Created**: 2026-05-17
> **Source**: [openclaw/openclaw](https://github.com/openclaw/openclaw) 源码分析

---

## 1. SQLite 数据库设计

### task_runs 主表

```sql
CREATE TABLE IF NOT EXISTS task_runs (
  task_id TEXT PRIMARY KEY,           -- 任务唯一标识
  runtime TEXT NOT NULL,              -- "local" / "acp"
  task_kind TEXT,                     -- 任务类型
  source_id TEXT,                     -- 来源 ID
  requester_session_key TEXT,         -- 谁发起的
  owner_key TEXT NOT NULL,            -- 所属者
  scope_kind TEXT NOT NULL,           -- 作用域类型
  child_session_key TEXT,             -- 子会话（执行用）
  parent_flow_id TEXT,                -- 所属任务流
  parent_task_id TEXT,                -- 父任务
  agent_id TEXT,                      -- Agent 标识
  run_id TEXT,                        -- 执行批次 ID（每次重试生成新的）
  label TEXT,                         -- 显示名
  task TEXT NOT NULL,                 -- 任务描述/prompt
  status TEXT NOT NULL,               -- 状态机核心字段
  delivery_status TEXT NOT NULL,      -- 交付状态 "pending"/"delivered"
  notify_policy TEXT NOT NULL,        -- "silent" / "state_changes"
  created_at INTEGER NOT NULL,
  started_at INTEGER,
  ended_at INTEGER,
  last_event_at INTEGER,
  cleanup_after INTEGER,              -- 过期清理时间戳
  error TEXT,
  progress_summary TEXT,
  terminal_summary TEXT,
  terminal_outcome TEXT               -- "blocked" / null
);
```

### task_delivery_state 交付状态表

```sql
CREATE TABLE IF NOT EXISTS task_delivery_state (
  task_id TEXT PRIMARY KEY,
  requester_origin_json TEXT,         -- JSON: {channel, to, accountId, threadId}
  last_notified_event_at INTEGER
);
```

### 索引

```sql
CREATE INDEX idx_task_runs_run_id ON task_runs (run_id);
CREATE INDEX idx_task_runs_status ON task_runs (status);
CREATE INDEX idx_task_runs_runtime_status ON task_runs (runtime, status);
CREATE INDEX idx_task_runs_cleanup_after ON task_runs (cleanup_after);
CREATE INDEX idx_task_runs_last_event_at ON task_runs (last_event_at);
CREATE INDEX idx_task_runs_owner_key ON task_runs (owner_key);
CREATE INDEX idx_task_runs_parent_flow_id ON task_runs (parent_flow_id);
CREATE INDEX idx_task_runs_child_session_key ON task_runs (child_session_key);
```

### 设计原理

- **主表 + 交付表分离**：交付信息（发给谁、通过什么渠道）只在交付时需要，不用每次加载任务都带上
- **run_id 索引**：每次重试生成新的 run_id，可追溯每次执行的独立历史
- **owner_key 索引**：按用户查询该用户的所有任务
- **cleanup_after 索引**：高效扫描过期记录
- **UPSERT 策略**：`INSERT ... ON CONFLICT(task_id) DO UPDATE SET ...`，同一 task_id 安全重入

---

## 2. 状态机设计

### 状态定义

```
ACTIVE_STATUSES   = { "queued", "running" }
FAILURE_STATUSES  = { "failed", "timed_out", "lost" }
TERMINAL_STATUSES = { "succeeded", "failed", "timed_out", "cancelled", "lost" }
```

### 状态转换图

```
                    ┌─────────┐
                    │ queued  │  ← 初始状态
                    └────┬────┘
                         │ executor 开始执行
                         ▼
                    ┌─────────┐
            ┌──────│ running  │──────┐
            │      └────┬────┘      │
            │           │           │
     执行成功    执行失败    超时     被取消     进程丢失
         │         │         │         │          │
         ▼         ▼         ▼         ▼          ▼
    ┌─────────┐ ┌───────┐ ┌────────┐ ┌──────────┐ ┌──────┐
    │succeeded│ │failed │ │timed_out│ │cancelled │ │ lost │
    └─────────┘ └───┬───┘ └────────┘ └──────────┘ └──────┘
                    │
                    │ 重试策略允许时
                    ▼
               回到 queued（新 run_id）
```

### 合法转换规则

```python
valid_transitions = {
    "queued":   → ["running"],
    "running":  → ["succeeded", "failed", "timed_out", "cancelled"],
    # 终态不可再转换（除 failed → queued 重试）
}
```

### 设计原理

- **单向终态**：非终态→终态是单向的，防止状态回退导致逻辑混乱
- **重试回环**：`failed → queued` 是唯一的例外，通过新 `run_id` 追踪每次重试
- **并发保护**：`isValidTransition()` 校验每次变更，防止两个线程同时把 running 转为不同终态

---

## 3. 每步输入/输出伪代码

### Step 1: Cron 触发

```
输入:  cron 表达式到期
输出:  jobId → activateJob(jobId)

┌──────────────┐     定时器到期      ┌───────────────────┐
│ cron scheduler│ ──────────────────→│ activeJobsManager │
└──────────────┘                     └────────┬──────────┘
                                              │
                              markCronJobActive(jobId)
                              加入内存 Set<string>
                                              │
                                              ▼
                                     taskRegistry.createTask()
```

**原理**：`activeJobsManager` 是全局单例 `Set<string>`，记录当前活跃的 cron job。防止同一个 job 被并发触发两次。

---

### Step 2: 任务注册（TaskRegistry）

```
输入:  { taskId, task(prompt), ownerKey, runtime, notifyPolicy }
输出:  TaskRecord (写入 SQLite + 内存索引)

function createTaskRecord(input):
    record = {
        task_id:     generateId(),
        run_id:      generateId(),
        status:      "queued",
        owner_key:   input.ownerKey,
        task:        input.prompt,
        runtime:     input.runtime,
        created_at:  now(),
        delivery_status: "pending",
        notify_policy: input.notifyPolicy
    }

    // 双写：内存 + SQLite
    memoryIndex[record.task_id] = record
    memoryIndex.byRunId[record.run_id] = record
    memoryIndex.byOwnerKey[record.owner_key] = record

    sqlite.execute("""
        INSERT INTO task_runs (task_id, run_id, status, owner_key, ...)
        VALUES (?, ?, 'queued', ?, ...)
        ON CONFLICT(task_id) DO UPDATE SET ...
    """, [record...])

    // 同时写交付状态表
    sqlite.execute("""
        INSERT INTO task_delivery_state (task_id, requester_origin_json)
        VALUES (?, ?)
    """, [record.task_id, JSON(origin)])

    emit("task.created", record)
    return record
```

**数据流转**：`input → TaskRecord → {内存索引(3个) + SQLite(2张表) + 事件广播}`

**原理**：
- **双写**：内存索引提供 O(1) 查询，SQLite 提供持久化和崩溃恢复
- **多索引**：byRunId（执行追踪）、byOwnerKey（按用户查询）、byTaskId（精确查找）
- **upsert**：`ON CONFLICT DO UPDATE` 保证同一 task_id 安全重入

### Step 2.1: TaskRecord 类型定义

```typescript
type TaskRuntime = "subagent" | "acp" | "cli" | "cron";
type TaskScopeKind = "session" | "system";

TaskRecord = {
  taskId: string;                    // 任务唯一标识
  runtime: TaskRuntime;              // 来源：cron定时 / subagent子代理 / acp / cli
  taskKind?: string;                 // 任务类型细分
  sourceId?: string;                 // 来源 ID（如 cron jobId）
  requesterSessionKey: string;       // 谁发起的
  ownerKey: string;                  // 所属者（peer_key）
  scopeKind: TaskScopeKind;          // 作用域：session=用户级别 / system=系统级别
  childSessionKey?: string;          // 子会话（实际执行用的隔离 session）
  parentFlowId?: string;             // 所属任务流（多步任务编排）
  parentTaskId?: string;             // 父任务
  agentId?: string;                  // Agent 标识
  runId?: string;                    // 执行批次 ID（每次重试生成新的）
  label?: string;                    // 显示名
  task: string;                      // 任务描述/prompt
  status: TaskStatus;                // 状态机当前状态
  deliveryStatus: TaskDeliveryStatus;// 交付状态
  notifyPolicy: TaskNotifyPolicy;    // 通知策略
  createdAt: number;                 // 创建时间戳
  startedAt?: number;                // 开始执行时间戳
  endedAt?: number;                  // 结束时间戳
  lastEventAt?: number;              // 最后事件时间戳
  error?: string;                    // 错误信息
  terminalSummary?: string;          // 终态摘要
  terminalOutcome?: "succeeded" | "blocked";
};
```

### Step 2.2: 多维索引设计与使用场景

同一个 TaskRecord，不同模块用不同的"关键词"来查找。底层是倒排索引，用内存 Map 手工实现。

假设一个任务创建后：

```
TaskRecord {
  taskId: "task_abc123",
  runId: "run_xyz789",
  ownerKey: "telegram:bot1:user_张三",
  parentFlowId: "flow_001",
  childSessionKey: "sess_isolated_456",
  status: "running",
  ...
}
```

#### 索引结构

```typescript
// 模块级全局变量（非类实例，任何模块可直接 import 调用）
const tasks = new Map<string, TaskRecord>();                   // 主表：taskId → record
const taskIdsByRunId = new Map<string, Set<string>>();         // runId → Set<taskId>
const taskIdsByOwnerKey = new Map<string, Set<string>>();      // ownerKey → Set<taskId>
const taskIdsByParentFlowId = new Map<string, Set<string>>();  // flowId → Set<taskId>
const taskIdsByRelatedSessionKey = new Map<string, Set<string>>(); // sessionKey → Set<taskId>
const tasksWithPendingDelivery = new Set<string>();            // 待投递的 taskId 集合
```

#### 使用场景

| 场景 | 触发方 | 只知道什么 | 通过哪个索引 | 查询方式 |
|---|---|---|---|---|
| cron 执行完，更新结果 | CronService | `runId = "run_xyz789"` | taskIdsByRunId | O(1) 直接定位 |
| 张三问"我的任务怎样了" | 聊天界面 | `ownerKey = "telegram:bot1:user_张三"` | taskIdsByOwnerKey | O(1) 拿到该用户所有任务 |
| flow 管理器查子任务 | TaskFlow 编排器 | `parentFlowId = "flow_001"` | taskIdsByParentFlowId | O(1) 拿到流下所有任务 |
| 投递系统发结果给用户 | Delivery 模块 | 无特定 key | tasksWithPendingDelivery | 遍历待投递集合 |

如果没有索引，每次查询要遍历全部任务做 `filter`，O(n)。有了索引是 O(1) Map 查找。

#### 索引维护

```
创建任务：
  tasks.set(taskId, record)                    主表
  addRunIdIndex(runId, taskId)                 runId 索引
  addOwnerKeyIndex(ownerKey, taskId)           ownerKey 索引
  addParentFlowIdIndex(flowId, taskId)         flow 索引
  addRelatedSessionKeyIndex(sessionKey, taskId) session 索引
  persistTaskUpsert(record)                    SQLite 持久化

删除任务：
  tasks.delete(taskId)
  deleteRunIdIndex / deleteOwnerKeyIndex / ...  反向清理所有索引
  persistTaskDelete(taskId)                     SQLite 删除
```

#### 存储架构

```
┌─────────────────────────────────────────────┐
│  内存层（模块级全局变量）                      │
│                                             │
│  tasks = Map<taskId, TaskRecord>      主表  │
│  taskDeliveryStates = Map<taskId, State>    │
│                                             │
│  多维索引（倒排索引）：                        │
│  taskIdsByRunId       = Map<runId, Set<id>> │
│  taskIdsByOwnerKey    = Map<key, Set<id>>   │
│  taskIdsByParentFlowId = Map<flowId, Set<>> │
│  taskIdsByRelatedSessionKey = Map<sk, Set<>>│
│  tasksWithPendingDelivery = Set<id>         │
└──────────────────────┬──────────────────────┘
                       │ 双写（增量 upsert）
                       ▼
┌─────────────────────────────────────────────┐
│  SQLite 持久化层                              │
│                                             │
│  upsertTaskWithDeliveryState()  ← 增量写入   │
│  deleteTaskWithDeliveryState()  ← 增量删除   │
│  loadSnapshot()                 ← 启动恢复   │
└─────────────────────────────────────────────┘
```

**设计要点**：
- 运行时所有查询走内存（O(1)），SQLite 只做持久化保底
- 持久化是增量 upsert，不是每次全量快照；只在启动时 `loadSnapshot()` 全量加载
- 全局 Map 而非类实例，任何模块直接 `import` 调用，不需要传引用

---

### Step 3: 任务调度（三层架构）

> **核心发现**：OpenClaw 的"调度"不是"派发任务给别人执行"，而是"谁触发谁执行"。
> TaskExecutor 不负责执行——它只管 TaskRecord 的生命周期状态转换。

#### 3.1 整体架构：三层职责分离

```
┌──────────────────────────────────────────────────────┐
│  调用层（谁触发，谁执行）                                │
│                                                      │
│  Cron → executeJobCore() → executeDetachedCronJob()  │
│  Subagent → agent turn 直接执行                       │
│  ACP → agent turn 直接执行                            │
│  CLI → agent turn 直接执行                            │
└──────────────────┬───────────────────────────────────┘
                   │ 创建/更新 TaskRecord
                   ▼
┌──────────────────────────────────────────────────────┐
│  状态管理层（TaskExecutor + DetachedTaskRuntime）      │
│                                                      │
│  TaskExecutor: 生命周期门面（Facade）                  │
│    createQueuedTaskRun()                              │
│    createRunningTaskRun()    ← Cron 直接用这个        │
│    startTaskRunByRunId()     ← queued → running      │
│    completeTaskRunByRunId()  ← → succeeded           │
│    failTaskRunByRunId()      ← → failed/timed_out    │
│                                                      │
│  DetachedTaskRuntime: 可插拔适配器（Strategy）         │
│    默认：委托给 TaskExecutor                           │
│    插件：可覆盖所有生命周期方法                         │
│    钩子：tryRecoverTaskBeforeMarkLost                 │
└──────────────────┬───────────────────────────────────┘
                   │ 读写数据
                   ▼
┌──────────────────────────────────────────────────────┐
│  数据层（TaskRegistry）                                │
│  内存索引 + SQLite 双写（见 Step 2）                   │
└──────────────────────────────────────────────────────┘
```

#### 3.2 Cron 触发的完整调度链路

以 Cron 任务为例，从 `onTimer()` 到执行的完整流程：

```
onTimer()
  │
  ├─ collectRunnableJobs(state, now)      // 筛选到期 job
  │   └─ isRunnableJob(): enabled? runningAtMs==null? nextRunAtMs <= now?
  │
  ├─ locked(state, () => {                // 加锁保护 store
  │     job.state.runningAtMs = now       // 标记正在执行
  │     persist(state)                    // 持久化防并发
  │   })
  │
  └─ Worker Pool 执行（并发度可配置，默认=1）
     │
     runDueJob(job):
       │
       ├─ 1. markCronJobActive(job.id)    // 全局 Set 防重入
       ├─ 2. emit("started")              // 事件广播
       │
       ├─ 3. tryCreateCronTaskRun()       // 创建 TaskRun 记录
       │     → createRunningTaskRun({     // 直接创建为 running 状态
       │         runtime: "cron",
       │         sourceId: job.id,
       │         scopeKind: "system",
       │         deliveryStatus: "not_applicable",
       │         notifyPolicy: "silent",
       │       })
       │     → TaskRegistry 双写（内存 + SQLite）
       │
       ├─ 4. executeJobCoreWithTimeout()  // 实际执行！Cron 自己执行
       │     → Promise.race([
       │         executeJobCore(state, job),  // 核心执行
       │         timeoutPromise               // 超时保护
       │       ])
       │     executeJobCore():
       │       job.sessionTarget === "main" ?
       │         → executeMainSessionCronJob()  // 主会话（系统事件/agent turn）
       │         → executeDetachedCronJob()     // 隔离会话（独立 session 执行）
       │
       └─ 5. applyOutcomeToStoredJob()    // 结果写回 job state
             → clearCronJobActive()
             → tryFinishCronTaskRun()     // 更新 TaskRun 终态
                  → completeTaskRunByRunId() / failTaskRunByRunId()
             → applyJobResult()           // 计算下次执行时间、错误退避
             → persist(state)
             → armTimer(state)            // 重新设置定时器
```

#### 3.3 TaskExecutor：生命周期管理门面

`task-executor.ts` 不是执行器，而是封装 TaskRegistry 底层操作的高层 API：

```typescript
// task-executor.ts 导出的核心函数：

createQueuedTaskRun(params)    → TaskRecord(queued)    // 创建排队任务
createRunningTaskRun(params)   → TaskRecord(running)   // 直接创建运行中任务

startTaskRunByRunId(params)    → queued → running      // 开始执行
recordTaskRunProgressByRunId() → 更新 progress         // 进度更新
completeTaskRunByRunId(params) → → succeeded           // 成功完成
failTaskRunByRunId(params)     → → failed/timed_out    // 执行失败
finalizeTaskRunByRunId(params) → → 任意终态            // 统一终态入口
```

每个函数内部都调用 `runtime-internal.ts`（即 TaskRegistry 的底层操作），完成：
1. 状态转换校验（isValidTransition）
2. 内存索引更新
3. SQLite upsert

#### 3.4 DetachedTaskRuntime：可插拔策略

```typescript
// detached-task-runtime-contract.ts 定义的接口
interface DetachedTaskLifecycleRuntime {
  createQueuedTaskRun(params): TaskRecord
  createRunningTaskRun(params): TaskRecord
  startTaskRunByRunId(params): TaskRecord[]
  completeTaskRunByRunId(params): TaskRecord[]
  failTaskRunByRunId(params): TaskRecord[]
  cancelDetachedTaskRunById(params): Promise<CancelResult>

  // 钩子：maintenance 标记 lost 前的最后恢复机会
  tryRecoverTaskBeforeMarkLost?(params): RecoveryResult
}
```

设计意图：
- **默认实现**：直接委托给 `task-executor.ts` 的函数
- **插件注册**：`registerDetachedTaskRuntime(pluginId, runtime)` 可完全替换行为
- **恢复钩子**：`tryRecoverTaskBeforeMarkLost` — 在维护任务把 stale 任务标为 lost 之前，给插件一次恢复机会（如重启进程、重新连接会话）

#### 3.5 不同 Runtime 的调度路径

| Runtime | 触发方 | 执行方式 | TaskRun 创建方式 |
|---|---|---|---|
| `cron` | CronService.onTimer() | Cron 自己调 `executeJobCore` → agent turn | `createRunningTaskRun`（直接 running） |
| `subagent` | 父 agent 发起 | agent turn 在隔离 session 执行 | `createQueuedTaskRun` 或 `createRunningTaskRun` |
| `acp` | ACP 协议请求 | agent turn 执行 | `createRunningTaskRun` |
| `cli` | CLI 命令行 | 直接在主进程执行 | 通常不创建 TaskRun |

**设计原理**：
- **谁触发，谁执行**：Cron 不把任务"派发"给 TaskExecutor 执行，而是自己执行
- **TaskExecutor 只管状态**：统一管理所有 TaskRecord 的生命周期转换，不管具体执行逻辑
- **DetachedTaskRuntime 提供扩展点**：插件可替换生命周期行为，如自定义任务恢复策略
- **TaskFlow 编排**：TaskExecutor 还管理多步任务流（parentFlowId），支持 blocked 重试和级联取消

---

### Step 4: 任务执行（多层 Watchdog 超时保护）

#### 4.1 整体结构

```
executeJobCoreWithTimeout()
  │
  ├─ createCronAgentWatchdog()    ← 创建看门狗
  │
  ├─ Promise.race([               ← 赛跑：谁先完成取谁
  │     executeJobCore(),          // 正常执行（一路喂狗）
  │     timeoutPromise             // 超时信号
  │  ])
  │
  └─ watchdog.dispose()           ← 无论成功失败，清理所有计时器
```

#### 4.2 Watchdog 分层闹钟机制

Watchdog = 分层闹钟。业务每推进一步就取消旧闹钟、设新闹钟。闹钟响了说明卡住了，直接 abort。

```
                          注册 runner 启动
                               │
 ┌──────────────────┐         ▼        ┌──────────────────────┐
 │waiting_for_runner│ ────────────────→│waiting_for_execution │
 │ (闹钟1: 60s)      │                  │(闹钟2: timeout/2)     │
 └────────┬─────────┘                   └──────────┬───────────┘
          │ 超时 → abort                           │ 检测到实际执行
          ▼                                        ▼
    ┌──────────┐                         ┌───────────┐
    │ timed_out│                         │ executing │
    └──────────┘                         │(闹钟3: jobTimeout)│
                                         └─────┬─────┘
                                               │ 超时 → abort
                                               ▼
                                         ┌──────────┐
                                         │ timed_out│
                                         └──────────┘
```

| 层 | 触发条件 | 超时时间 | 含义 |
|---|---|---|---|
| 闹钟1 | `watchdog.start()` | 60s | Runner 都没启动？可能进程挂了 |
| 闹钟2 | `noteRunnerStarted()` | max(1s, jobTimeout/2) | Runner 启动了但还没真正执行？可能卡在认证/加载 |
| 闹钟3 | `noteRunnerStarted()` | jobTimeoutMs（可配置） | 执行太久？可能 LLM 调用卡住了 |

#### 4.3 阶段契约（Runner → Watchdog 的通信协议）

Runner 执行时只能报预定义的阶段名（`CronAgentExecutionPhase` 枚举），不能随便编：

```
Runner 的合法阶段名（按执行顺序）：

  runner_entered        ← runner 刚启动
  workspace             ← 加载工作区
  runtime_plugins       ← 加载插件
  model_resolution      ← 解析用哪个模型
  auth                  ← API 认证
  context_engine        ← 上下文引擎准备
  before_agent_reply    ← 准备回复
  attempt_dispatch      ← 开始发请求
  context_assembled     ← 上下文拼好了
  process_spawned       ← 进程启动了
  tool_execution_started← 工具在执行
  assistant_output_started ← 模型开始输出了
  model_call_started    ← 模型调用开始了
  turn_accepted         ← 一轮对话完成
```

Watchdog 内部有一张映射表，把具体阶段名归到两大类：

```
pre_execution（准备阶段）         execution（执行阶段）
─────────────────────────       ─────────────────────────
runner_entered                   attempt_dispatch
workspace                        context_assembled
runtime_plugins                  process_spawned
model_resolution                 tool_execution_started
auth                             assistant_output_started
context_engine                   model_call_started
before_agent_reply               turn_accepted
```

**两层解耦**：Runner 不认识 Watchdog，Watchdog 不认识 Runner。中间通过调用方中转，phase 名经过映射表转换。

#### 4.4 数据流

```
方向1: 正常执行 → 喂狗（回调）

  Runner                     调用方(executeJobCore)          Watchdog
    │                              │                            │
    ├─ "我到 auth 了" ──────────────→│                            │
    │                              ├─ notePhase("auth") ──────→  │
    │                              │                            ├─ 查表: auth → pre_execution
    │                              │                            ├─ 还在准备阶段，闹钟2继续跑
    │                              │                            │
    ├─ "我到 model_call_started" ──→│                            │
    │                              ├─ notePhase(...) ─────────→  │
    │                              │                            ├─ 查表: → execution
    │                              │                            ├─ 进入执行阶段！cancel(闹钟2)

方向2: 超时 → abort

  Watchdog 闹钟响了
    → triggerTimeout(reason)
    → abortSignal.abort()          ← 通知 executeJobCore 别跑了
    → Promise.race 返回超时结果
```

#### 4.5 具体时间线示例

```
场景: Cron job "每天9点总结工作日志"，jobTimeout = 300s

T=0s    watchdog.start()                    ← 设闹钟1: 60s
T=2s    noteRunnerStarted()                 ← cancel(闹钟1), 设闹钟2: 150s, 闹钟3: 300s
T=5s    notePhase("runtime_plugins")        ← pre_execution, 不动
T=8s    notePhase("auth")                   ← pre_execution, 不动
T=15s   notePhase("model_call_started")     ← execution! cancel(闹钟2)
T=45s   任务完成
        watchdog.dispose()                  ← cancel(闹钟3)
```

#### 4.6 重试策略

执行失败后，不是立刻重试，而是指数退避回到 `queued`：

```
失败 → shouldRetry?
  ├─ Yes: record.run_id = newId()
  │       record.status = "queued"
  │       applyJobResult() 计算下次执行时间
  │       nextRunAtMs = max(自然下次执行时间, 退避时间)
  │       // 退避公式: errorBackoffMs(consecutiveErrors)
  │       // 如: 1次错误→30s, 2次→60s, 3次→120s...
  │
  └─ No:  record.status = "failed"
          错误计数器 consecutiveErrors 累加
          连续错误超阈值 → 自动 disable job + 发告警
```

**幂等 upsert**：重复执行不创建重复记录（`ON CONFLICT(task_id) DO UPDATE`）。

---

### Step 5: 状态管理（状态机校验）

```
输入:  currentStatus, targetStatus
输出:  校验通过 → 更新 status；校验失败 → 报错

function updateStatus(record, newStatus):
    if newStatus not in valid_transitions[record.status]:
        throw InvalidTransition(record.status, newStatus)
    record.status = newStatus
    record.last_event_at = now()
    upsertToSqlite(record)
```

**原理**：防止并发场景下的状态混乱——两个线程同时想把 running 转为不同终态时，只有第一个会成功。

---

### Step 6: 结果交付（幂等）

```
输入:  TaskRecord (status=terminal, delivery_status=pending)
输出:  消息送达用户 / 无操作

function maybeDeliverTaskTerminalUpdate(record):
    // 1. 检查是否终态
    if record.status not in TERMINAL_STATUSES:
        return

    // 2. 检查交付策略
    if record.notify_policy == "silent":
        return

    // 3. 检查交付状态（幂等保护）
    if record.delivery_status != "pending":
        return                              // 已交付过

    // 4. 幂等键防重
    key = idempotencyKey(record.run_id)
    if alreadyDelivered(key):
        return

    // 5. 查询交付目标
    deliveryState = sqlite.query("""
        SELECT requester_origin_json
        FROM task_delivery_state
        WHERE task_id = ?
    """, [record.task_id])

    origin = JSON.parse(deliveryState.requester_origin_json)
    // origin = { channel: "telegram", to: "user_123", accountId: "bot_1" }

    // 6. 根据来源渠道交付
    message = formatTerminalMessage(record, origin)
    if origin:
        session.deliverMessage(origin, message)      // 直接送达用户
    else:
        systemEventQueue.push(message)               // 系统事件队列

    // 7. 标记已交付
    record.delivery_status = "delivered"
    upsertToSqlite(record)
    markDelivered(key)
```

**数据流转**：
```
terminal record
  → 检查 notify_policy ≠ silent
  → 检查 delivery_status == pending（幂等）
  → 查 task_delivery_state 拿到 origin
  → 按 origin 渠道投递
  → 标记 delivery_status = "delivered"
```

**三层幂等保护**：
1. `delivery_status` 字段 —— 数据库层面标记
2. `idempotencyKey(run_id)` —— 内存层面去重
3. `notify_policy` —— 业务层面过滤

**为什么要这么重？** 分布式系统中网络抖动可能导致完成通知被发送多次。没有幂等保护，用户会收到多条 "任务完成" 消息。

**origin 的意义**：记录任务从哪个渠道发起，结果就原路返回。用户从 Telegram 发起 → 结果回 Telegram。

---

### Step 7: 清理持久化

```
输入:  所有 terminal 状态且 cleanup_after 已过期的 TaskRecord
输出:  内存索引移除，SQLite 记录保留

function cleanup():
    now = currentTime()
    expired = sqlite.query("""
        SELECT * FROM task_runs
        WHERE cleanup_after IS NOT NULL
          AND cleanup_after < ?
          AND status IN ('succeeded','failed','timed_out','cancelled','lost')
    """, [now])

    for record in expired:
        archive(record)
        releaseTempResources(record.child_session_key)
        memoryIndex.delete(record.task_id)
```

**原理**：`cleanup_after` 在任务终态时设置（如 succeeded 后 1 小时清理）。到期后清理内存索引但保留 SQLite 记录，用于历史查询。

---

## 4. 整体数据流全景

```
Cron到期
  │
  ▼
activeJobsManager.markActive(jobId)     ← 防并发
  │
  ▼
TaskRegistry.createTaskRecord()         ← SQLite + 内存索引
  │   输出: TaskRecord(queued)
  │
  ▼
TaskExecutor.execute(record)            ← 超时 + 重试
  │   输出: TaskRecord(succeeded/failed/...)
  │
  ▼
状态机校验 isValidTransition()          ← 防非法状态
  │
  ▼
maybeDeliverTaskTerminalUpdate()        ← 幂等交付
  │   查 task_delivery_state → origin → 原路投递
  │
  ▼
Maintenance.cleanup()                   ← 过期清理
```

## 5. 与 mini-claw 的核心差异

| 问题 | mini-claw | OpenClaw |
|---|---|---|
| 并发安全 | 无保护 | activeJobs Set 防重入 |
| 崩溃恢复 | JSONL 追加，重启丢失内存状态 | SQLite 双写，重启可恢复 |
| 执行失败 | 记录后结束 | 重试策略 + 新 run_id 回环 |
| 交付重复 | 可能重复投递 | 三层幂等保护 |
| 状态管理 | success: True/False | 6 态状态机 + 转换校验 |

每一步多出来的设计都是为了解决**生产环境的可靠性问题**。
