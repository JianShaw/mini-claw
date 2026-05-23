# Agent 工作台实现方案

> **给 Claude：** 实施本方案时，必须使用 `superpowers:executing-plans`，并按任务逐步执行。

**目标：** 构建 Web 端 Agent 工作台，让用户在开始对话前，可以编辑 Agent 的提示词、工具、技能和可调运行参数。

**架构：** 复用现有 Agent REST API，并新增一个轻量的工具目录接口，用于前端发现可用工具、后端校验 Agent 配置。前端新增一个聚焦的管理页面，通过 `PUT /api/v1/agents/{id}` 更新已有 Agent 记录。

**重要边界：** 当前 `claw` 层的 provider 和 model 由 `DeepSeekAgentRunner` 固定，不在 Web 工作台中开放选择。Web 端只允许编辑 Agent 自身配置，以及当前 runner 支持的可调参数，例如 `temperature`。

**技术栈：** FastAPI、Pydantic、SQLite-backed AgentStore、React、TypeScript、Tailwind。

---

## 范围

本阶段包含：

- 新增 `GET /api/v1/tools`，用于列出当前可配置的内置工具。
- 在 `PUT /api/v1/agents/{id}` 中校验 `enabled_tools`、`enabled_skills` 和可调 `model_config`。
- 新增前端 API helper，支持获取 Agent 详情、更新 Agent、获取工具列表。
- 新增 `/agents` 路由和 Agent 工作台页面。
- 支持用户保存 Agent 名称、系统提示词、启用技能、启用工具和 `temperature`。
- 支持用户从某个 Agent 直接创建新对话。

本阶段不包含：

- provider 选择。
- model 选择。
- MCP server 管理界面。
- per-Agent runtime isolation 重构。
- 多用户权限。
- Expert 导入/导出。

## 后端任务

### 任务 1：工具 Schema 和 Router

涉及文件：

- 新建：`web/backend/schemas/tool.py`
- 新建：`web/backend/routers/tools.py`
- 修改：`web/backend/app.py`

步骤：

1. 新增 `ToolSchema`，字段包括 `name`、`description`、`parameters`。
2. 新增 `GET /tools` 路由，返回已经注册的内置工具。
3. 在 `web/backend/app.py` 中把 tools router 注册到 `/api/v1` 下。
4. 在 `app.state.tools_registry` 上保存共享工具注册表，确保工具列表展示和 Agent 配置校验使用同一份工具目录。

### 任务 2：Agent 更新校验

涉及文件：

- 修改：`web/backend/routers/agents.py`
- 测试：`tests/test_web_api.py`
- 测试：`tests/test_web_tool_api.py`

步骤：

1. 使用 `SkillsRegistry` 校验更新请求中的 `enabled_skills`。
2. 使用 `app.state.tools_registry` 校验更新请求中的 `enabled_tools`。
3. 将 `model_config` 限制为当前 runner 允许 Web 调整的字段：`temperature`。
4. 对未知技能、未知工具、`provider`、`name` 或其他非法模型配置返回 422。
5. 保持原有局部更新行为不变：请求中未传入的字段不改动。

## 前端任务

### 任务 3：Client API

涉及文件：

- 修改：`web/frontend/src/api/client.ts`

步骤：

1. 新增 `ToolInfo` 类型。
2. 新增 `fetchTools()`。
3. 新增 `fetchAgent(agentId)`。
4. 新增 `updateAgent(agentId, req)`。

### 任务 4：Agent 工作台 UI

涉及文件：

- 新建：`web/frontend/src/components/AgentWorkbench.tsx`
- 修改：`web/frontend/src/App.tsx`

步骤：

1. 新增 `/agents` 路由和侧边栏入口。
2. 页面加载时获取 agents、skills 和 tools。
3. 渲染 Agent 列表和 Agent 详情编辑器。
4. 支持通过复选框启用/禁用技能和工具。
5. 不提供 provider/model 下拉框或输入框，只显示模型由 claw 运行层固定。
6. 通过 `updateAgent` 保存配置。
7. 支持从当前选中的 Agent 创建新对话。

## 验证

执行：

- `uv run pytest tests/test_web_api.py tests/test_web_tool_api.py -v`
- `uv run pytest tests/test_web_skill_api.py tests/test_web_task_api.py -v`
- `cd web/frontend && npm run build`
- 时间允许时执行全量测试：`uv run pytest`

