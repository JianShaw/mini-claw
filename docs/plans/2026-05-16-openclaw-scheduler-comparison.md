# OpenClaw vs mini-claw 定时任务架构对比

> **Created**: 2026-05-16
> **Source**: [openclaw/openclaw](https://github.com/openclaw/openclaw) 官方仓库源码分析

## 核心模块对比

| 模块 | OpenClaw | mini-claw | 差距 |
|---|---|---|---|
| 触发器 | `ActiveJobsManager` (src/cron/) | `_interval_loop` / `_cron_loop` asyncio | 已对齐 |
| 任务注册 | `TaskRegistry` + SQLite 持久化 | `dict` 内存管理 | 缺注册中心 |
| 执行引擎 | `TaskExecutor` + 策略 | `_execute_by_name` 直接执行 | 缺策略控制 |
| 后台任务 | `DetachedTaskRuntime` 独立进程 | 无 | 缺独立进程 |
| 状态机 | queued→running→succeeded/failed/timed_out/cancelled/lost | success: True/False | 缺状态机 |
| 执行策略 | `task-executor-policy.ts` 重试/超时/幂等 | 无 | 缺策略 |
| 结果交付 | `maybeDeliverTaskTerminalUpdate()` 幂等交付 | delivery.send() 直接交付 | 缺幂等 |
| 执行历史 | SQLite | JSONL | 存储差异 |

## 完整流程对比

### OpenClaw 完整流程（7 步）

```
1. Cron 触发
   ActiveJobsManager 监听定时器 → activateJob()

2. 任务创建（TaskRegistry）
   createTaskRecord(taskId, runId)
   → 写入内存索引 + SQLite 持久化
   → 建立多种索引（by runId, ownerKey, sessionKey）
   → 发出任务创建事件

3. 任务调度
   TaskRegistry 根据任务类型选择执行器
   → detached 任务 → DetachedTaskRuntime（独立进程）
   → 常规任务 → TaskExecutor

4. 任务执行
   TaskExecutor 应用策略（重试/超时）
   → 调用 agent session
   → 通过事件机制报告状态变化

5. 状态管理
   执行器 → onAgentEvent() → TaskRegistry 更新
   状态转换: queued → running → succeeded/failed/timed_out/cancelled/lost
   isValidStatusTransition() 校验

6. 结果交付
   maybeDeliverTaskTerminalUpdate()
   → 检查请求者 Origin
   → 有 Origin：直接消息交付到 session
   → 无 Origin：系统事件队列交付
   → idempotencyKey 防重复

7. 清理持久化
   更新 cleanupAfter → 持久化最终状态 → 清理临时资源
```

### mini-claw 流程（3 步）

```
1. 触发
   _interval_loop / _cron_loop asyncio 循环到期

2. 执行
   _execute_by_name(name)
   → LLM 任务: _trigger_llm_task → InboundMessage → gateway → session → agent → delivery
   → 系统任务: _execute_handler → 直接调 handler(context, **params)

3. 记录
   TaskRunHistory.record() → JSONL 追加写入
```

## 已对齐的部分

- 消息走 gateway → session → agent → delivery 的管道（LLM 任务）
- 双模式执行：LLM 任务走管道，系统任务直调
- 执行历史持久化
- 配置文件驱动（JSON）

## 待补齐的特性（按优先级）

1. **执行策略**：重试（RetryPolicy）、超时（TimeoutPolicy）— 提高可靠性
2. **状态机**：完整的状态转换校验 — 防止非法状态
3. **TaskRegistry**：持久化注册中心 + 多索引查询 — 多用户场景需要
4. **幂等交付**：idempotencyKey 防重复 — 多渠道场景需要
5. **DetachedTaskRuntime**：独立进程执行 — 长时间任务需要
6. **SQLite 持久化**：替代 JSONL — 大量历史记录场景需要

## OpenClaw 关键源码文件索引

| 文件 | 职责 |
|---|---|
| `src/cron/active-jobs.ts` | Cron 作业激活与管理 |
| `src/tasks/task-registry.ts` (65KB) | 任务注册中心，核心枢纽 |
| `src/tasks/task-registry.store.sqlite.ts` | SQLite 持久化存储 |
| `src/tasks/task-executor.ts` (18KB) | 任务执行引擎 |
| `src/tasks/task-executor-policy.ts` | 重试/超时/交付策略 |
| `src/tasks/task-status.ts` | 状态枚举与转换校验 |
| `src/tasks/detached-task-runtime.ts` | 独立进程任务运行时 |
| `src/tasks/task-flow-registry.ts` (21KB) | 任务流（DAG 编排） |
| `src/tasks/task-registry.maintenance.ts` (36KB) | 任务清理与维护 |
