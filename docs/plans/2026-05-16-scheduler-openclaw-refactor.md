# 定时任务架构重构：OpenClaw 统一消息流

> **Status**: DONE
> **Created**: 2026-05-16
> **Based on**: 2026-05-15-scheduled-task-system.md

## Context

原调度器绕过 gateway/agent 管道直接调 handler，结果仅记日志。
按 OpenClaw 模式重构为双模式架构：
- **LLM 任务**（peer_key + prompt）：生成 InboundMessage → gateway 全链路（session → agent → delivery）
- **系统任务**（handler）：保留直调，但增加 run history 持久化

## 架构对比

```
OpenClaw:
  Cron 触发 → 生成 message → 送进 session → AgentRunner → delivery → run history

Mini-claw 重构后:
  LLM 任务:  触发 → 构建 InboundMessage → gateway.handle_inbound_message → session → agent → delivery → history
  系统任务:  触发 → handler(task_context, **params) → TaskResult → history
```

## 两种任务模式

| | LLM 任务 | 系统任务 |
|---|---|---|
| **配置** | `peer_key` + `prompt` | `handler` (callable/dotted path) |
| **执行** | InboundMessage → gateway → session → agent → delivery | 直接调 handler |
| **结果** | 自然进入 session history + delivery | TaskResult → run history |
| **示例** | 喝水提醒、定时问候 | daily_distill、memory_update、idle_compact |

## 改动文件

| 文件 | 改动 |
|------|------|
| `claw/scheduler/types.py` | TaskDefinition 新增 `peer_key`/`prompt`/`is_llm_task`；新增 `TaskRunRecord` |
| `claw/scheduler/history.py` | **新建** — `TaskRunHistory` JSONL 执行记录存储 |
| `claw/scheduler/scheduler.py` | 构造函数改 `(gateway, context, history)`；新增 `_trigger_llm_task`；`_execute_by_name` 分支双模式 |
| `claw/scheduler/context.py` | 删除 `dispatch_to_peer`（LLM 任务改由 scheduler 走 gateway） |
| `claw/scheduler/tasks.py` | 删除 `llm_prompt_task`（功能由 scheduler 内置） |
| `claw/scheduler/config.py` | 解析/序列化 `peer_key`/`prompt` 字段 |
| `claw/scheduler/__init__.py` | 新增导出 `TaskRunRecord`/`TaskRunHistory` |
| `claw/agent.py` | 适配新构造函数；`create_scheduled_task` 使用 `peer_key`+`prompt` |
| `schedule_config.json` | LLM 任务迁移为 `peer_key`/`prompt` 格式 |
| `tests/test_scheduler.py` | 更新为新构造函数，新增 LLM 任务 + run history 测试 |
| `tests/test_scheduler_tasks.py` | 删除 `llm_prompt_task` 测试 |
| `tests/test_scheduler_config.py` | 新增 `peer_key`/`prompt` 解析测试 |
| `tests/test_scheduler_history.py` | **新建** — TaskRunHistory 测试 |

## 测试验证

- 354 passed, 2 skipped
