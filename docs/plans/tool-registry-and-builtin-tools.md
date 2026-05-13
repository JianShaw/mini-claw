# Plan: ToolRegistry 集成 LLM Function Calling + 社区工具

## Context

当前 `claw/tools.py` 有基础的 `ToolsRegistry`（注册/查找/列举），但：
- `Tool` 缺少 JSON Schema 参数定义（无法传递给 LLM function calling API）
- `DeepSeekAgentRunner` 完全不使用工具（纯文本对话）
- `ChatMessage` 不支持 tool_call / tool_result 类型的消息
- JSONL 持久化不支持工具调用消息
- 没有实际可用的社区工具

**目标**：让 Agent 能通过 LLM function calling 自动调用注册的工具，并内置搜索、命令行、文件操作等常用工具。

---

## 设计决策

1. **扩展 ChatMessage（不新增消息类型）** — 在 `ChatMessage` 上加 `tool_calls`/`tool_call_id`/`tool_name` 可选字段，保持 `list[ChatMessage]` 同构，避免所有消费者级联改动。
2. **迭代式工具执行循环** — `while` 循环处理 LLM 返回的 tool_calls，有 `max_iterations` 上限防无限循环。
3. **最小化额外依赖** — calculator/file_ops/time 用标准库，shell 用 `asyncio.subprocess`，web_search 使用 `duckduckgo-search` 库（零 API key）。
4. **Gateway 不变** — 工具执行完全在 AgentRunner 内部，Gateway 不感知工具细节。
5. **Shell 黑名单过滤** — 禁止 `rm -rf /`、`format`、`del /s` 等危险命令模式，其余放行。

---

## 实现步骤

### Phase 1: 扩展数据类型

**修改 `claw/types.py`**
- `ChatMessage` 新增三个可选字段：
  - `tool_calls: list[dict[str, Any]] | None` — assistant 消息携带的工具调用列表
  - `tool_call_id: str | None` — tool 消息对应的调用 ID
  - `tool_name: str | None` — tool 消息对应的工具名称
- `StreamChunk.type` 扩展为 `Literal["thinking", "content", "system", "tool_call", "tool_result"]`

### Phase 2: 增强 ToolsRegistry

**修改 `claw/tools.py`**
- `Tool` dataclass 新增 `parameters: dict[str, Any] | None`（JSON Schema）
- `ToolsRegistry` 新增两个方法：
  - `to_openai_tools() -> list[dict]` — 生成 OpenAI function calling 格式的工具定义
  - `async execute(name, arguments) -> Any` — 按名称查找并执行工具 handler

### Phase 3: JSONL 持久化支持工具消息

**修改 `claw/session.py`**
- `_read_history()` — 反序列化时读取 `tool_calls`、`tool_call_id`、`tool_name`（用 `.get()` 保持向后兼容）
- `_append_messages()` — 序列化时写入工具相关字段

### Phase 4: DeepSeek 工具调用集成

**修改 `claw/deepseek.py`**
- `__init__` 新增参数 `tools_registry: ToolsRegistry | None` 和 `max_tool_iterations: int = 10`
- 新增 `_build_kwargs(messages)` — 构建带 tools 参数的 API 调用参数
- 修改 `_build_messages(session)` — 正确序列化 tool/tool_call 消息到 API messages
- 修改 `run()` — 实现工具执行循环：
  1. LLM 返回 tool_calls → 执行每个工具 → 将结果加入 messages → 继续循环
  2. LLM 无 tool_calls → 提取文本返回
  3. 超过 max_iterations → 返回提示信息
- 修改 `run_stream()` — 流式场景下的工具调用处理（收集 tool_call delta → 执行工具 → 重新调用 API）

### Phase 5: 依赖注入

**修改 `claw/agent.py`**
- `MiniClaw.__init__` 新增 `tools_registry: ToolsRegistry | None` 参数
- 传递给 `DeepSeekAgentRunner(tools_registry=...)`

### Phase 6: 社区工具

**新建 `claw/builtin_tools/__init__.py`** — `register_all(registry)` 便捷函数

**新建 `claw/builtin_tools/calculator.py`**
- 安全数学求值（`ast.parse` 白名单），拒绝 `__import__` 等危险调用
- 参数：`expression: string`

**新建 `claw/builtin_tools/shell.py`**
- `asyncio.create_subprocess_shell` 执行命令，超时控制（默认 30s）
- **黑名单过滤**：禁止 `rm -rf /`、`mkfs`、`format`、`dd if=`、`del /s /q`、`shutdown`、`:(){:|:&};:` 等危险命令模式
- 返回 stdout + stderr + return code
- 参数：`command: string`, `timeout: number`

**新建 `claw/builtin_tools/file_ops.py`**
- `file_read` — 读取文件内容
- `file_write` — 写入文件
- `file_list` — 列出目录内容
- 参数：`path: string`

**新建 `claw/builtin_tools/web_search.py`**
- 使用 `duckduckgo-search` 库实现真正的搜索能力（零 API key）
- `uv add duckduckgo-search` 添加依赖
- 两个工具：
  - `web_search` — 搜索关键词，返回搜索结果列表
  - `web_fetch` — 抓取指定 URL 内容返回文本（基于 urllib）
- 参数：`query: string` / `url: string`

**新建 `claw/builtin_tools/time_tool.py`**
- 返回当前日期时间
- 无参数

---

## 关键文件清单

| 文件 | 变更类型 |
|------|---------|
| [claw/types.py](claw/types.py) | 修改 - ChatMessage 扩展 |
| [claw/tools.py](claw/tools.py) | 修改 - Tool 增加 parameters, Registry 增加 to_openai_tools/execute |
| [claw/session.py](claw/session.py) | 修改 - JSONL 持久化工具消息 |
| [claw/deepseek.py](claw/deepseek.py) | 修改 - 工具调用循环 |
| [claw/agent.py](claw/agent.py) | 修改 - 注入 ToolsRegistry |
| `claw/builtin_tools/__init__.py` | 新建 |
| `claw/builtin_tools/calculator.py` | 新建 |
| `claw/builtin_tools/shell.py` | 新建 |
| `claw/builtin_tools/file_ops.py` | 新建 |
| `claw/builtin_tools/web_search.py` | 新建 |
| `claw/builtin_tools/time_tool.py` | 新建 |

---

## 测试计划

### 新增/修改测试文件

| 测试文件 | 覆盖范围 |
|---------|---------|
| `tests/test_types.py` | ChatMessage 新字段默认值、独立性 |
| `tests/test_tools.py` | `to_openai_tools()` 格式、`execute()` 调用/异常 |
| `tests/test_jsonl_session_store.py` | 工具消息 JSONL 往返、向后兼容旧记录 |
| `tests/test_deepseek.py` | 工具循环、错误处理、迭代上限、流式工具调用 |
| `tests/test_agent.py` | MiniClaw 传递 registry 到 runner |
| `tests/test_builtin_tools.py` | 每个社区工具的正常/异常场景 |

### 关键边界用例
- `tool_calls=[]` 空列表 → 视为无工具调用
- arguments 中非法 JSON → 捕获异常返回错误信息
- 工具 handler 抛异常 → 捕获并作为错误结果返回给 LLM
- 注册表中找不到工具名 → KeyError 返回错误
- 流式场景中多个 tool_call delta 聚合
- 压缩器处理工具消息 → 已有逻辑兼容（`m.role + m.content` 字符串拼接）
- shell 黑名单命令被拦截并返回错误提示

### 验证步骤
1. `uv run pytest` 全量通过
2. `uv run mini-claw-chat` 启动后对话正常
3. 在对话中触发工具调用（如"计算 2+3"、"列出当前目录"、"搜索 Python 最新版本"等）

---

## 实现顺序

Phase 1 → 2 → 3 → 4 → 5 → 6（每步完成后运行对应测试）
