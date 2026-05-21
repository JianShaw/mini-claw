# Web 管理端定时任务功能

> 日期：2026-05-20（更新：2026-05-21）
> 分支：feat/expert-square-web

## Context

项目已有完整的自研调度器 `claw/scheduler/`，但只通过 CLI 命令管理。Web 管理端需要新增定时任务的增删改查、启停、手动触发、执行历史查看功能。

**核心需求变更（2026-05-21）**：创建定时任务时用户选择专家角色（agent），系统自动创建专用推送会话（session_type="scheduled"），而非手动填写 peer_key。定时推送会话在对话列表中独立分组展示。

## 模块边界设计

四层分离，每层只依赖下一层的公共接口：

```
┌─────────────────────────────────────────────┐
│  Router 层 (web/backend/routers/tasks.py)   │  HTTP 端点，零业务逻辑
│  只调用 TaskService，不导入 claw.scheduler.*  │
├─────────────────────────────────────────────┤
│  Schema 层 (web/backend/schemas/task.py)    │  Pydantic 请求/响应模型
│  纯数据定义，不依赖任何业务模块                │
├─────────────────────────────────────────────┤
│  Service 层 (web/backend/services/           │
│              task_service.py)                │  Web 层唯一入口
│  协调 Scheduler + Config + History           │  只调用 Scheduler 公共 API
│  强制：Web 只管理 LLM 任务，系统任务只读       │
│  创建任务时自动创建推送 session                │
├─────────────────────────────────────────────┤
│  Core 层 (claw/scheduler/)                   │  已有模块，不修改
│  scheduler.py / config.py / history.py       │
│  types.py / executor.py / context.py         │
└─────────────────────────────────────────────┘
```

### 边界规则

| 规则 | 说明 |
|------|------|
| Router → Service | Router 不导入 `claw.scheduler.*`，只调用 `TaskService` |
| Service → Scheduler | Service 只调用 Scheduler 公共方法，不访问 `_` 前缀属性 |
| 先持久化再更新内存 | 所有 Web 变更先写 `schedule_config.json`，再更新内存中的 Scheduler |
| 系统任务只读 | handler 模式的任务：列表可见，不可通过 Web 创建/编辑/删除 |
| Schema 零业务依赖 | Pydantic 模型只做数据序列化/校验，不含业务逻辑 |
| Session 自动管理 | 创建任务时自动创建专用 session，删除任务时清理 session |

### 各层职责明细

**Schema 层** (`web/backend/schemas/task.py`)：
- `TriggerSchema` — 触发器配置（type + expression/seconds + event_name/idle_timeout_seconds）
- `CreateTaskRequest` — 创建请求，`agent_id` 必填（替代 peer_key）
- `UpdateTaskRequest` — 更新请求，description/trigger/prompt/enabled 可选
- `ToggleRequest` — 启停请求
- `TaskSchema` — 任务列表响应（含 agent_id, session_id）
- `TaskDetailSchema(TaskSchema)` — 任务详情响应（含 history）
- `TaskRunRecordSchema` — 执行记录响应
- `TriggerResultSchema` — 手动触发结果

**Service 层** (`web/backend/services/task_service.py`)：
- 拥有 `TaskScheduler` 实例，管理其生命周期
- 持有 `TaskDefinition` 缓存（`_definitions: dict`），避免每次从文件重载
- 持有 `session_id` 缓存（`_session_map: dict`），关联任务与会话
- 所有变更走 `config.upsert_task_config()` 持久化
- 对外返回纯 dict（TaskView），不暴露内部 dataclass
- 创建任务时：agent_id → gateway.create_session_for_agent() → 标记 session_type="scheduled" → 从 session 提取 peer_key
- 删除任务时：清理关联 session

**Router 层** (`web/backend/routers/tasks.py`)：
- 8 个端点，薄薄一层 HTTP 适配
- 错误处理：`ValueError` → 400/404/403
- 从 `app.state.task_service` 获取服务实例

## 新建文件

| 文件 | 职责 |
|------|------|
| `web/backend/services/__init__.py` | 包初始化（空） |
| `web/backend/services/task_service.py` | 服务门面：Web ↔ Scheduler 的唯一桥梁 |
| `web/backend/schemas/task.py` | Pydantic 请求/响应模型 |
| `web/backend/routers/tasks.py` | FastAPI 路由，REST 端点 |
| `web/frontend/src/components/TaskManager.tsx` | 前端任务管理 UI（agent 选择下拉框） |
| `tests/test_task_service.py` | TaskService 单元测试 |
| `tests/test_web_task_api.py` | HTTP 集成测试 |

## 修改文件

| 文件 | 修改内容 |
|------|----------|
| `web/backend/app.py` | 引入 TaskService + lifespan 管理 + 注册 tasks router |
| `web/backend/routers/conversations.py` | list_conversations 增加 type 过滤参数 + session_type 输出 |
| `web/backend/schemas/conversation.py` | ConversationListItem 增加 session_type 字段 |
| `web/frontend/src/api/client.ts` | CreateTaskRequest: peer_key → agent_id；增加 agent_id/session_id/session_type 字段 |
| `web/frontend/src/components/ConversationList.tsx` | 按会话类型分组：普通对话 / 定时推送 |
| `web/frontend/src/App.tsx` | 侧边栏添加"定时任务"导航 + Route |

## API 设计

前缀 `/api/v1/tasks`

| 方法 | 路径 | 功能 | 说明 |
|------|------|------|------|
| GET | `/tasks` | 任务列表 | 返回所有任务（含系统任务，标记 task_type） |
| GET | `/tasks/{name}` | 任务详情 + 历史 | |
| POST | `/tasks` | 创建 LLM 任务 | 选择 agent_id → 自动创建推送 session |
| PUT | `/tasks/{name}` | 更新 LLM 任务 | 仅 LLM 任务可编辑，系统任务返回 403 |
| PATCH | `/tasks/{name}/toggle` | 启用/禁用 | 所有任务都可切换 |
| POST | `/tasks/{name}/trigger` | 手动触发 | 等待执行完成返回结果 |
| GET | `/tasks/{name}/history` | 执行历史 | `?limit=20` |
| DELETE | `/tasks/{name}` | 删除任务 | 同时清理关联 session |

### 对话列表过滤

`GET /api/v1/conversations?type=scheduled|normal` — 按 session_type 过滤

## TaskService 公共接口

```python
class TaskService:
    """定时任务管理服务：Web 层与调度器之间的唯一入口。"""

    def __init__(
        self,
        gateway: RuntimeGateway,
        config_path: str = "schedule_config.json",
        history_path: str = "data/scheduler/history.jsonl",
    ) -> None

    async def start(self) -> None
        """加载配置 → 注册任务 → 恢复 session 映射 → 启动调度器。"""

    async def stop(self) -> None
        """停止调度器。"""

    def list_tasks(self) -> list[dict]
        """返回所有已注册任务的摘要视图。"""

    def get_task(self, name: str) -> dict | None
        """返回单个任务详情（含最近执行历史）。"""

    async def create_task(
        self, *, name: str, trigger: Trigger, agent_id: str,
        prompt: str, description: str = "", enabled: bool = True,
    ) -> dict
        """创建 LLM 任务：自动创建 session → 持久化 → 注册到 scheduler。"""

    async def update_task(self, name: str, updates: dict) -> dict
        """更新 LLM 任务。"""

    async def toggle_task(self, name: str, enabled: bool) -> dict
        """切换任务启用状态。"""

    async def trigger_task(self, name: str) -> dict
        """手动触发。"""

    async def delete_task(self, name: str) -> None
        """删除 LLM 任务 + 清理关联 session。"""

    def get_history(self, task_name: str, limit: int = 20) -> list[dict]
        """查询执行历史。"""
```

## 核心数据流

### 创建任务（新流程）
`POST /tasks {agent_id, trigger, prompt}` → Router → TaskService.create_task()
1. 验证任务名唯一
2. 通过 `gateway.create_session_for_agent()` 创建专用推送 session
   - peer_id: `sched:{task_name}`
   - metadata 标记 `session_type="scheduled"`, `task_name=name`
3. 从 session 中提取 `peer_key`（= session.session_key）
4. 构建 `TaskDefinition`（含 peer_key + prompt + params.session_id）
5. `upsert_task_config()` 持久化到 schedule_config.json
6. `scheduler.register()` 加入内存调度
7. 更新 `_definitions` 和 `_session_map` 缓存
8. 返回 TaskView dict（含 agent_id, session_id）

### 删除任务
`DELETE /tasks/{name}` → Router → TaskService.delete_task()
1. 验证任务存在且为 LLM 任务
2. `scheduler.unregister()` 从内存移除
3. 从 schedule_config.json 删除配置
4. `gateway.delete_session()` 清理关联推送 session
5. 清理缓存

### Session 恢复
服务启动时，从 config 中已有任务的 `params.session_id` 恢复 `_session_map`。

## 会话类型标记

定时推送 session 的 metadata：
```python
session.metadata = {
    "session_type": "scheduled",
    "task_name": "morning_greeting",
}
```

对话列表按 `session_type` 分组：
- `normal` — 普通对话，用户可主动发消息
- `scheduled` — 定时推送，只接收定时推送内容

## 生命周期集成

在 `app.py` 中使用 FastAPI lifespan：

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    await app.state.task_service.start()
    yield
    await app.state.task_service.stop()

app = FastAPI(title="Mini Claw Web API", version="0.1.0", lifespan=lifespan)
```

## 前端设计

**TaskManager.tsx**：单页面组件，包含：

- **任务卡片列表**：名称、描述、触发类型/值、任务类型徽章（LLM/系统）、关联专家、启用状态、上次结果
- **操作按钮**：编辑（仅 LLM）、启停、手动触发、查看历史、删除（仅 LLM）
- **系统任务**：显示但操作按钮减弱（只读 + 可启停）
- **创建表单**（模态框）：名称、描述、专家选择下拉框（从 agents API 获取）、触发类型选择器（cron/interval）、prompt
- **历史面板**：执行记录表格（时间、成功/失败、消息、错误）

**ConversationList.tsx**：按 session_type 分组：
- **对话** — 普通会话列表
- **定时推送** — scheduled 类型会话（琥珀色样式，无删除按钮）

---

## TODO 实施清单

### Phase 1: Schema 变更

- [x] **TODO-1.1**: 修改 `web/backend/schemas/task.py`
  - `CreateTaskRequest`: `peer_key` → `agent_id`
  - `TaskSchema`: 增加 `agent_id`, `session_id` 可选字段
  - `TriggerSchema`: 增加 `event` 类型支持
- [x] **TODO-1.2**: 修改 `web/backend/schemas/conversation.py`
  - `ConversationListItem`: 增加 `session_type: str | None = None`

### Phase 2: TaskService 变更

- [x] **TODO-2.1**: 修改 `web/backend/services/task_service.py`
  - `__init__`: 增加 `self._session_map: dict[str, str] = {}`
  - `create_task()`: 改签名接收 agent_id → 创建 session → 提取 peer_key → 缓存 session_id
  - `start()`: 启动时从配置恢复 `_session_map`
  - `delete_task()`: 增加清理关联 session 逻辑
  - `_build_task_view()`: 输出增加 `agent_id`, `session_id`
- [x] **TODO-2.2**: 更新 `tests/test_task_service.py`
  - create_task 测试改用 agent_id
  - 验证 session 被创建 + session_type 标记
  - 验证 delete 清理 session
  - 验证 session 恢复

### Phase 3: Router 变更

- [x] **TODO-3.1**: 修改 `web/backend/routers/tasks.py`
  - `create_task()`: 从 body 取 agent_id 传给 service
- [x] **TODO-3.2**: 修改 `web/backend/routers/conversations.py`
  - `list_conversations()`: 增加 `type` 过滤参数
  - `_session_to_list_item()`: 输出 `session_type`
- [x] **TODO-3.3**: 更新 `tests/test_web_task_api.py`
  - create task 测试改用 agent_id

### Phase 4: 前端变更

- [x] **TODO-4.1**: 修改 `web/frontend/src/api/client.ts`
  - `CreateTaskRequest`: `peer_key` → `agent_id`
  - `ScheduledTask`: 增加 `agent_id`, `session_id`
  - `UpdateTaskRequest`: 移除 `peer_key`
  - `ConversationListItem`: 增加 `session_type`
  - `fetchConversations()`: 增加 `type` 参数
- [x] **TODO-4.2**: 修改 `web/frontend/src/components/TaskManager.tsx`
  - 创建表单：peer_key 输入 → agent 选择下拉框（fetchAgents）
  - 任务卡片显示关联专家名
  - 编辑时隐藏 agent 选择（创建后不可更改）
- [x] **TODO-4.3**: 修改 `web/frontend/src/components/ConversationList.tsx`
  - 按 session_type 分组：普通对话 / 定时推送
  - 定时推送卡片使用琥珀色样式

### Phase 5: 验证闭环

- [x] **TODO-5.1**: `pytest tests/test_task_service.py tests/test_web_task_api.py` — 35 passed
- [x] **TODO-5.2**: `pytest` 全量测试通过 — 729 passed, 2 skipped
- [x] **TODO-5.3**: codex:adversarial-review 代码审查

---

## Phase 6: 调度器执行管道重构（定时任务不走 Gateway）

**动机**：定时任务（LLM 类型）原来通过构造 `InboundMessage` 走 `Gateway.handle_inbound_message()` 全链路。Gateway 是为外部通道消息设计的，定时任务是内部调度不应绕道。逻辑分散在 executor.py 的平铺函数中，缺少清晰分层。

**目标架构**：
```
CronScheduler (scheduler.py)
  ↓ 发现 due task
TaskQueue (scheduler.py 内部)
  ↓ 排队、防重复、并发控制
TaskRunner (runner.py)
  ↓ 消费任务
TaskConfigResolver (runner.py 内部方法)
  ↓ 解析 agent/prompt/session
AgentRunService (agent_run.py)
  ↓ 加载 session → 注入 context → 调用 AgentRunner
AgentRunner (已有的 ContextBuildingAgentRunner)
  ↓ 执行 LLM + Tool Loop
DeliveryRouter (agent_run.py 内部)
  ↓ 保存 session + 可选通知
Channel / Session
```

### 变更清单

| 文件 | 变更 |
|------|------|
| `claw/scheduler/types.py` | 新增 `AgentRun` 数据类 |
| `claw/scheduler/agent_run.py` | **新建**：`AgentRunService` 类 |
| `claw/scheduler/runner.py` | **新建**：`TaskRunner` 类 |
| `claw/scheduler/scheduler.py` | 构造函数改接收 `AgentRunService`，`_do_execute` 委托给 `TaskRunner` |
| `claw/scheduler/executor.py` | **删除**（被 runner.py + agent_run.py 替代） |
| `claw/scheduler/__init__.py` | 导出 `AgentRun`, `TaskRunner`, `AgentRunService` |
| `claw/agent.py` | MiniClaw 构造 `AgentRunService` 传给 `TaskScheduler` |
| `web/backend/app.py` | `_build_default_gateway` 返回共享组件，构造 `AgentRunService` |
| `web/backend/services/task_service.py` | 新增 `agent_run_service` 参数 |
| `tests/test_scheduler.py` | `_mock_scheduler` 改用 `AgentRunService` mock |
| `tests/test_task_service.py` | fixture 新增 `mock_agent_run_service` |
| `tests/test_web_task_api.py` | fixture 构造 `AgentRunService` 实例 |

- [x] **TODO-6.1**: `claw/scheduler/types.py` 新增 AgentRun
- [x] **TODO-6.2**: 新建 `claw/scheduler/agent_run.py`
- [x] **TODO-6.3**: 新建 `claw/scheduler/runner.py`
- [x] **TODO-6.4**: 修改 `claw/scheduler/scheduler.py`
- [x] **TODO-6.5**: 修改 Web 层 (`app.py`, `task_service.py`)
- [x] **TODO-6.6**: 删除 `claw/scheduler/executor.py`
- [x] **TODO-6.7**: 更新 `claw/scheduler/__init__.py`
- [x] **TODO-6.8**: 更新测试，全量 729 passed, 2 skipped
- [ ] **TODO-6.9**: codex:adversarial-review 审查，修复问题

---

## 复用的已有模块（不修改）

| 模块 | 复用点 |
|------|--------|
| `claw/scheduler/scheduler.py` | `TaskScheduler` 公共 API |
| `claw/scheduler/config.py` | `ScheduleConfigLoader`, `upsert_task_config()`, `validate_cron()` |
| `claw/scheduler/history.py` | `TaskRunHistory` |
| `claw/scheduler/types.py` | `TaskDefinition`, trigger 类型, `TaskResult`, `TaskRunRecord` |
| `claw/gateway.py` | `create_session_for_agent()`, `delete_session()`, `list_sessions()` |
| `claw/session.py` | `build_peer_key()` |
| `claw/channels/web/adapter.py` | `WEB_*` 常量 |
| `claw/types.py` | `Session.metadata` — 标记 session_type，无需改 dataclass |
