# 专家广场 + 对话列表 Web UI 实现方案（V1/V2 - Expert/Agent/Session 三层架构）

## Context

当前 mini-claw 是纯 CLI 应用，需要新增 Web 端支持：专家广场（浏览/安装专家）、对话列表（管理会话）、流式聊天界面。

**架构核心**：Expert（模板）→ Agent（运行实例）→ Session（对话）三层分离。Session 绑定 Agent，不直接绑定 Expert。Agent 从 Expert 创建，继承默认配置，用户可独立修改。

**技术栈**：后端 FastAPI + 前端 React/Vite/TailwindCSS + 流式 SSE

---

## 版本拆分

### V1 交付方案（当前实施范围）

目标是先做出可用的 Web 版闭环，同时把核心三层架构立住，避免第一版就重构半个运行时。

V1 包含：
- Expert / Agent / Session 三层模型
- SQLite 存储：`data/mini_claw.sqlite`
- 默认 `default-agent`
- 专家 CRUD：Web 提供入口，核心规则在 `claw/expert/service.py`
- Web channel 抽象：`WebTransport → WebAdapter → ChannelProcessor → Gateway`
- Gateway 支持显式 `session_id`，避免 Web 多对话串 active session
- Agent 动态配置：`system_prompt`、`model_config`
- tools / skills / MCP：先使用现有全局 registry，在运行时按 Agent 配置过滤可见能力
- FastAPI API + React UI：专家广场、Agent 管理、对话列表、流式聊天

V1 不做：
- 每个 Agent 独立 `ToolsRegistry / SkillsRegistry / McpManager`
- 每个 Agent 独立 MCP 连接池和缓存失效
- SQLite → EXPERT.md 导出
- 通用多平台 channel 配置中心
- `["*"]` 通配符语义

### V2 演进方案（V1 稳定后）

V2 解决 Agent 运行时能力隔离和可扩展性问题。

V2 包含：
- `AgentRuntimeManager`：按 Agent 配置构建专属 runtime
- per-Agent `ToolsRegistry / SkillsRegistry / McpManager`
- MCP server 按 Agent 连接、注册、缓存、失效
- Agent 配置更新后 runtime cache 自动失效
- SQLite → EXPERT.md 导出
- 更完整的 channel 管理能力（多用户、多租户、多平台复用）
- 更细粒度的 tool/skill/mcp 权限审计

---

## 概念边界

```
Plugin = 安装包（未来扩展）
Expert = 专家模板（EXPERT.md 定义，不可变）
Agent  = 从 Expert 创建的可运行实例（可修改）
Session= 某个 Agent 下的一次对话
Skill  = Agent 按需加载的能力说明包
MCP    = Agent 可连接的外部工具服务
Tool   = Agent 可直接调用的本地工具
```

**核心流程**：
```
专家广场浏览 Expert → 安装 Expert → 创建 Agent 实例 → 创建 Session(agent_id) → 聊天
```

**为什么不直接 Session 绑定 Expert？**
- Session 应只负责会话记录，不承担配置职责
- Agent 可被用户独立编辑（修改 system_prompt、增减 skills）
- Expert 模板可升级，不影响已创建的 Agent
- 一个 Expert 可创建多个 Agent（不同配置）

---

## 目录结构

```
mini-claw/
  claw/
    expert/                        # NEW - 专家模板管理
      __init__.py
      types.py                     # Expert, ExpertMeta 数据类
      store.py                     # ExpertStore (SQLite 持久化 + EXPERT.md 导入/导出)
      registry.py                  # ExpertRegistry (注册/查询/搜索)
      marketplace.py               # 专家安装/卸载操作
      bundled/                     # 内置专家
        general-assistant/EXPERT.md
        code-helper/EXPERT.md
        paper-reader/EXPERT.md
    agent_runtime/                 # NEW - Agent 运行实例管理
      __init__.py                  #   命名 agent_runtime 而非 agent，
      types.py                     #   因为 claw/agent.py 已存在（MiniClaw facade）
      store.py                     # AgentStore (SQLite 持久化)
      factory.py                   # AgentFactory (从 Expert 创建 Agent)
      resolver.py                  # AgentResolver / RuntimeProfile（按 session.agent_id 解析运行配置）
      runtime.py                   # V2 - AgentRuntimeManager（按 Agent 配置安装/注册 skills/tools/MCP）
    gateway.py                     # MODIFY - 添加 agent_resolver 依赖
    deepseek.py                    # MODIFY - V1 支持按 Agent RuntimeProfile 动态使用 model/system prompt，并过滤 tools
    ports.py                       # MODIFY - 添加 AgentStore / AgentResolver / RuntimeProfile Protocol
    storage/
      __init__.py
      sqlite.py                    # SQLite 连接、schema migration、事务工具
  web/                             # NEW - Web 层
    __init__.py
    app.py                         # FastAPI 应用工厂
    deps.py                        # 依赖注入
    channel.py                     # WebTransport / WebAdapter：将 HTTP/SSE 请求适配为 PlatformEvent/InboundMessage
    routers/
      __init__.py
      experts.py                   # 专家 REST API
      agents.py                    # Agent REST API
      conversations.py             # 对话 REST API
      chat.py                      # 聊天 SSE 流式 API
    schemas/
      __init__.py
      expert.py                    # Pydantic 模型
      agent.py
      conversation.py
      chat.py
  web/frontend/                    # NEW - React 前端
    package.json / vite.config.ts / tailwind.config.js
    src/
      api/client.ts                # API 调用封装
      types/index.ts               # TypeScript 类型定义
      hooks/useSSE.ts              # SSE 流式 Hook
      components/
        Layout.tsx
        Sidebar.tsx
        ExpertMarketplace.tsx
        ConversationList.tsx
        ChatWindow.tsx
        MessageBubble.tsx
  data/
    mini_claw.sqlite               # NEW - Expert / Agent / Session / Message 元数据先统一存 SQLite
```

---

## 数据模型

### Expert (claw/expert/types.py) — 专家模板

```python
@dataclass(slots=True)
class ExpertMeta:
    version: str = "0.1.0"
    author: str = ""
    tags: list[str] = field(default_factory=list)
    category: str = ""
    avatar: str = ""               # emoji 或 URL
    extra: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class Expert:
    name: str                      # 唯一标识（小写字母数字+连字符）
    display_name: str              # 显示名称（如中文）
    description: str               # 简短描述
    system_prompt: str             # EXPERT.md body — 默认系统提示词
    default_skills: list[str] = field(default_factory=list)
    default_tools: list[str] = field(default_factory=list)
    default_mcp_servers: list[str] = field(default_factory=list)
    default_model: dict[str, Any] = field(default_factory=dict)     # provider, name, temperature
    default_memory: dict[str, Any] = field(default_factory=dict)
    default_sandbox: dict[str, Any] = field(default_factory=dict)
    meta: ExpertMeta = field(default_factory=ExpertMeta)
    source: str = "local"          # "bundled" | "local"
    path: str | None = None
```

### EXPERT.md 文件格式

```yaml
---
name: code-helper
display_name: Code Helper
description: 代码审查与调试专家
default_skills:
  - code-review
  - file-search
default_tools:
  - read_file
  - write_file
  - run_command
default_mcp_servers: []
default_model:
  provider: deepseek
  name: deepseek-chat
  temperature: 0.3
default_memory:
  enabled: true
default_sandbox:
  workspace_required: true
meta:
  version: "0.1.0"
  author: mini-claw
  tags: [code, development, review]
  category: development
  avatar: "🤖"
---

You are an expert programming assistant...
```

### AgentConfig (claw/agent_runtime/types.py) — 运行实例

```python
@dataclass(slots=True)
class AgentConfig:
    """从 Expert 创建的运行时 Agent 实例。"""
    id: str                           # 唯一 ID (ag_xxx)
    name: str                         # 用户可自定义
    source_expert: str                # 来源 Expert 名称
    system_prompt: str                # 从 Expert 复制，用户可编辑
    enabled_skills: list[str] = field(default_factory=list)
    enabled_tools: list[str] = field(default_factory=list)
    enabled_mcp_servers: list[str] = field(default_factory=list)
    model_config: dict[str, Any] = field(default_factory=dict)
    memory_config: dict[str, Any] = field(default_factory=dict)
    sandbox_config: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""              # ISO 8601
    updated_at: str = ""              # ISO 8601
```

### RuntimeProfile — 每轮实际执行配置

`AgentConfig` 是持久化配置，`RuntimeProfile` 是每轮运行前从 `session.agent_id` 解析出的有效配置。默认 Agent 也使用同一套 `AgentConfig`，避免 CLI/Web 走两套逻辑。

```python
@dataclass(slots=True)
class RuntimeProfile:
    agent_id: str
    system_prompt: str
    model_config: dict[str, Any]
    enabled_skills: list[str]
    enabled_tools: list[str]
    enabled_mcp_servers: list[str]
    memory_config: dict[str, Any]
    sandbox_config: dict[str, Any]
```

解析规则：
- `session.agent_id` 为空或找不到时，回退到 `default-agent`
- `default-agent` 是真实存储的 AgentConfig，不是硬编码分支
- Expert 只负责创建 Agent 的初始值；运行时永远按 AgentConfig 生效
- V1 要求动态生效：`system_prompt`、`model_config`
- V1 对 `enabled_skills`、`enabled_tools`、`enabled_mcp_servers` 采用“全局 registry + 按 Agent 配置过滤”的方式生效
- V2 再升级为 per-Agent runtime：独立 registry、独立 MCP 连接、缓存和失效

### Session — 无需修改

Session 已有 `agent_id: str` 字段（`claw/types.py`），无需新增任何字段。运行时通过 `session.agent_id` → `AgentResolver.resolve()` → `RuntimeProfile` 解析本轮有效配置。

### SQLite 存储模型（v1）

数据暂时统一存到 `data/mini_claw.sqlite`。不再为 Web 新增一套 `data/experts/index.json` / `data/agents/*.json`。`EXPERT.md` 仍作为导入/导出格式和 bundled seed 来源，但安装后的专家内容以 SQLite 为准。

核心表：

```sql
CREATE TABLE experts (
  name TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  description TEXT NOT NULL,
  system_prompt TEXT NOT NULL,
  default_skills_json TEXT NOT NULL DEFAULT '[]',
  default_tools_json TEXT NOT NULL DEFAULT '[]',
  default_mcp_servers_json TEXT NOT NULL DEFAULT '[]',
  default_model_json TEXT NOT NULL DEFAULT '{}',
  default_memory_json TEXT NOT NULL DEFAULT '{}',
  default_sandbox_json TEXT NOT NULL DEFAULT '{}',
  meta_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL,
  source_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE agents (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_expert TEXT NOT NULL,
  system_prompt TEXT NOT NULL,
  enabled_skills_json TEXT NOT NULL DEFAULT '[]',
  enabled_tools_json TEXT NOT NULL DEFAULT '[]',
  enabled_mcp_servers_json TEXT NOT NULL DEFAULT '[]',
  model_config_json TEXT NOT NULL DEFAULT '{}',
  memory_config_json TEXT NOT NULL DEFAULT '{}',
  sandbox_config_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(source_expert) REFERENCES experts(name)
);

CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  session_key TEXT NOT NULL,
  channel TEXT NOT NULL,
  account_id TEXT NOT NULL,
  peer_id TEXT NOT NULL,
  sender_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  summary TEXT,
  history_offset INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE session_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  tool_calls_json TEXT,
  tool_call_id TEXT,
  tool_name TEXT,
  reasoning_content TEXT,
  ts INTEGER,
  created_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE active_sessions (
  session_key TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
```

SQLite 策略：
- 使用 stdlib `sqlite3` 即可，先不引入 ORM；FastAPI 中用短事务，必要时放进线程池
- 初始化时执行轻量 migration，设置 `PRAGMA journal_mode=WAL`
- 所有写操作用事务包住，避免 JSON index 时代的 lost update
- JSON 字段只存配置对象/列表，查询条件常用字段保持独立列

---

## API 端点

Base: `/api/v1`

### 专家 API (web/routers/experts.py)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/experts` | 列出所有专家，支持 `?q=&tag=` 过滤 |
| GET | `/experts/{name}` | 获取专家详情 |
| POST | `/experts/{name}/install` | 安装内置专家到本地 |
| POST | `/experts/install-from-file` | 从文件安装 |
| DELETE | `/experts/{name}` | 卸载本地专家 |

**边界说明**：专家角色的增删改查由 Web 提供管理入口最合适，但 Web router 不承载业务规则。推荐分层：
- `web/routers/experts.py`：HTTP 参数校验、状态码、schema 转换
- `claw/expert/service.py`：专家 CRUD、安装/卸载、名称冲突、bundled/local 规则
- `claw/expert/store.py`：SQLite 读写、EXPERT.md 导入/导出

这样 Web 是当前主要管理界面，但 CLI、测试、未来 marketplace 也能复用同一套 ExpertService。

### Agent API (web/routers/agents.py)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/agents` | 从 Expert 创建 Agent `{expert_name, agent_name?}` |
| GET | `/agents` | 列出所有 Agent 实例 |
| GET | `/agents/{id}` | 获取 Agent 详情 |
| PUT | `/agents/{id}` | 更新 Agent（system_prompt, name, skills 等） |
| DELETE | `/agents/{id}` | 删除 Agent 实例 |

### 对话 API (web/routers/conversations.py)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/conversations` | 列出当前用户所有对话 |
| POST | `/conversations` | 创建对话 `{agent_id: string}` |
| GET | `/conversations/{id}` | 获取对话详情+消息历史 |
| DELETE | `/conversations/{id}` | 删除对话 |
| POST | `/conversations/{id}/compact` | 压缩上下文 |

### 聊天 API (web/routers/chat.py)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat/stream` | SSE 流式聊天 |

请求体：`{session_id: string, text: string}`

SSE 事件格式：
```
data: {"type": "thinking", "text": "..."}
data: {"type": "content", "text": "..."}
data: {"type": "tool_call", "text": "..."}
data: {"type": "tool_result", "text": "..."}
data: [DONE]
```

---

## 关键实现细节

### 1. 默认 Agent + 动态 RuntimeProfile 解析链路

```
session.agent_id
  → AgentResolver.resolve(session.agent_id)
  → RuntimeProfile(system_prompt, model_config, tools, skills, mcp_servers, memory, sandbox)
  → session.metadata["agent_runtime_profile"]
  → Runner 按 RuntimeProfile 动态构建 messages / model / tools
```

**默认 Agent 配置**：
- 首次启动时确保 SQLite `agents` 表中存在 `id="default-agent"` 的记录
- CLI 创建 Session 仍默认使用 `default-agent`
- Web 创建 Session 使用用户选择的 Agent ID
- 如果 Session 里的 Agent 被删除，运行时回退到 `default-agent`，并在 metadata 中标记 `agent_missing=true`

**AgentResolver**：
```python
class AgentResolver:
    def __init__(
        self,
        agent_store: AgentStore,
        *,
        default_agent_id: str = "default-agent",
    ) -> None:
        self._agent_store = agent_store
        self._default_agent_id = default_agent_id

    def resolve(self, agent_id: str | None) -> RuntimeProfile:
        agent = self._agent_store.get(agent_id or self._default_agent_id)
        if agent is None and agent_id != self._default_agent_id:
            agent = self._agent_store.get(self._default_agent_id)
        if agent is None:
            agent = self._agent_store.ensure_default()
        return RuntimeProfile(
            agent_id=agent.id,
            system_prompt=agent.system_prompt,
            model_config=dict(agent.model_config),
            enabled_skills=list(agent.enabled_skills),
            enabled_tools=list(agent.enabled_tools),
            enabled_mcp_servers=list(agent.enabled_mcp_servers),
            memory_config=dict(agent.memory_config),
            sandbox_config=dict(agent.sandbox_config),
        )
```

**gateway.py** 添加 `_inject_agent_runtime_profile` 方法：
```python
async def _inject_agent_runtime_profile(self, session: Session) -> None:
    """按 session.agent_id 解析本轮运行配置并注入 session metadata。"""
    if self._agent_resolver is None:
        session.metadata.pop("agent_runtime_profile", None)
        return
    profile = self._agent_resolver.resolve(session.agent_id)
    session.metadata["agent_runtime_profile"] = profile
```

在 `handle_inbound_message` / `handle_stream` 中，先解析 `RuntimeProfile`，再按 profile 注入 memory/skills/tools/model。CLI 代码路径也传入默认 resolver，因此 CLI 和 Web 使用同一套动态配置逻辑。

**deepseek.py** 修改：
```python
def _build_messages(self, session: Session) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    # 1. Agent system prompt（最高优先级）
    profile = session.metadata.get("agent_runtime_profile")
    agent_prompt = profile.system_prompt if profile else ""
    if agent_prompt:
        messages.append({"role": "system", "content": agent_prompt})
    # 2. 会话摘要
    # 3. 记忆上下文
    # 4. 技能列表
    # 5. 对话历史（现有逻辑不变）
```

`DeepSeekAgentRunner` 不能再只依赖初始化时固定的 model。V1 需要支持每轮从 profile 获取 model，并按 profile 过滤工具/技能/MCP：
- `model_config.name/provider/temperature`：覆盖本轮 LLM 参数
- `enabled_tools`：从现有全局 ToolsRegistry 中过滤本轮允许工具
- `enabled_mcp_servers`：从 MCP 命名空间工具中过滤本轮允许 server
- `enabled_skills`：从现有 SkillsRegistry 中过滤本轮 skills listing

### 2. V1 Runtime Capability 过滤

V1 先不创建 per-Agent registry / MCP connection。能力边界仍然由 AgentConfig 决定，但实现上使用现有全局 registry 做本轮过滤，以降低核心改造风险。

```python
profile = agent_resolver.resolve(session.agent_id)

session.metadata["agent_runtime_profile"] = profile
session.metadata["skills_listing"] = skills_registry.build_skills_listing(
    enabled_skills=profile.enabled_skills,
)

runner.run_stream(
    session,
    message,
    model_config=profile.model_config,
    enabled_tools=profile.enabled_tools,
    enabled_mcp_servers=profile.enabled_mcp_servers,
)
```

V1 过滤规则：
- `enabled_tools=[]` 表示不暴露工具
- `enabled_tools=["calculator", "file_search"]` 表示只暴露这些工具
- `enabled_mcp_servers=[]` 表示不暴露 MCP 工具
- `enabled_mcp_servers=["github"]` 表示只暴露 `github__*` 命名空间工具
- `enabled_skills=[]` 表示不注入 skills listing
- 不支持 `["*"]` 通配符，默认 Agent 显式列出允许项

### 2b. V2 AgentRuntimeManager — Agent 层安装/注册 skills、MCP、tools

skills、MCP、tools 的最终生效边界在 Agent 层。V2 中 AgentConfig 不再只是被 Runner “过滤读取”，而是用于构建该 Agent 的运行时能力集合。

```python
@dataclass(slots=True)
class AgentRuntime:
    profile: RuntimeProfile
    tools_registry: ToolsRegistry
    skills_registry: SkillsRegistry
    mcp_manager: McpManager | None


class AgentRuntimeManager:
    def __init__(
        self,
        *,
        builtin_tool_catalog: BuiltinToolCatalog,
        skill_store: SkillStore,
        mcp_config_store: McpConfigStore,
    ) -> None: ...

    async def get_runtime(self, profile: RuntimeProfile) -> AgentRuntime:
        """按 Agent 配置安装/注册运行能力；配置未变时可复用缓存。"""
        # 1. 注册本 Agent 允许的 builtin/local tools
        # 2. 从 SkillStore 加载并注册本 Agent 允许的 skills
        # 3. 连接本 Agent 允许的 MCP servers，并将其 tools 注册到该 Agent 的 ToolsRegistry
        # 4. 返回 Agent 专属 runtime，供 Gateway/Runner 本轮使用
```

V2 运行时策略：
- Builtin tools 不需要“安装”，但必须注册到 Agent 专属 `ToolsRegistry`
- Skills 从 SkillStore 安装/加载到 Agent 专属 `SkillsRegistry`
- MCP server 根据 `enabled_mcp_servers` 连接，并只把该 Agent 允许的 server tools 注册进 Agent 专属 `ToolsRegistry`
- AgentConfig 更新后，使该 Agent 的 runtime cache 失效；下一轮重新构建
- 空列表表示禁用；是否支持通配符由 V2 再评估
- 不在全局 registry 上按请求临时增删工具，避免多 Agent 并发串配置

```python
profile = agent_resolver.resolve(session.agent_id)
runtime = await agent_runtime_manager.get_runtime(profile)

session.metadata["agent_runtime_profile"] = profile
session.metadata["skills_listing"] = runtime.skills_registry.build_skills_listing()
runner.run_stream(
    session,
    message,
    model_config=profile.model_config,
    tools_registry=runtime.tools_registry,
)
```

### 3. AgentFactory — 从 Expert 创建 Agent

```python
class AgentFactory:
    def create_from_expert(self, expert_name: str, agent_name: str | None = None) -> AgentConfig:
        expert = self._expert_store.load(expert_name)
        agent = AgentConfig(
            id=f"ag_{uuid4().hex[:12]}",
            name=agent_name or expert.display_name,
            source_expert=expert.name,
            system_prompt=expert.system_prompt,        # 复制，非引用
            enabled_skills=list(expert.default_skills),
            enabled_tools=list(expert.default_tools),
            enabled_mcp_servers=list(expert.default_mcp_servers),
            model_config=dict(expert.default_model),
            memory_config=dict(expert.default_memory),
            sandbox_config=dict(expert.default_sandbox),
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._agent_store.save(agent)
        return agent
```

### 4. SQLite Store — Expert / Agent / Session 统一持久化

v1 先统一使用 `data/mini_claw.sqlite`，避免 JSON 文件、JSONL、Web 状态各存一份。现有 `JsonlSessionStore` 可以保留用于兼容/测试，但 Web 方案新增 `SqliteSessionStore`，并优先在 Web 和 default-agent 路径使用。

**新增/修改组件**：
- `claw/storage/sqlite.py`：连接工厂、schema migration、事务 helper、WAL 初始化
- `claw/expert/store.py`：`SqliteExpertStore`
- `claw/agent_runtime/store.py`：`SqliteAgentStore`
- `claw/session.py`：新增 `SqliteSessionStore`，实现现有 `SessionStore` Protocol

**事务边界**：
- 创建 Agent：插入 `agents`，无需额外 index
- 创建 Session：插入 `sessions`，更新 `active_sessions`
- 保存消息：向 `session_messages` 追加，更新 `sessions.updated_at`
- compact：更新 `sessions.summary/history_offset`，保留消息记录
- 删除 Session：删除 `sessions`，依赖 cascade 删除 messages，并清理 active session

**测试覆盖**：
- migration 可重复执行
- 并发创建多个 Agent 不丢记录
- 创建/切换/list active session 行为与现有 `JsonlSessionStore` 一致
- session messages 可恢复 tool call / tool result / reasoning_content

**默认 Agent 初始化**：
```python
def ensure_default(self) -> AgentConfig:
    agent = self.get("default-agent")
    if agent is not None:
        return agent
    agent = AgentConfig(
        id="default-agent",
        name="Default Agent",
        source_expert="general-assistant",
        system_prompt="You are Mini Claw, a helpful assistant.",
        enabled_skills=[],          # 空列表表示不启用技能；默认值由 factory/seed 明确写入
        enabled_tools=["calculator", "get_current_time", "file_search", "load_skill"],
        enabled_mcp_servers=[],
        model_config={"provider": "deepseek", "name": "deepseek-chat"},
        memory_config={"enabled": True},
        sandbox_config={"workspace_required": True},
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    self.save(agent)
    return agent
```

注意：V1 不支持 `["*"]` 通配符。`enabled_tools=[]` 和 `enabled_mcp_servers=[]` 均表示禁用；默认 Agent 必须显式列出允许的工具和 MCP server。

### 5. Web 作为正式 Transport/Adapter 进入 Gateway

**问题**：`/chat/stream` 接收 `session_id`，但现有 `RuntimeGateway.handle_stream` 通过 `_get_or_create_session` 按 peer_key 取 active session。Web 端如果固定 `peer_key = web:default:web` 且只走 active session，多对话/多标签页会串到同一个 active session。

**v1 决策**：保留 Web channel 抽象。Web 层不绕过 Gateway，而是作为系统的第二个正式 channel 接入：HTTP/SSE 负责 transport，WebAdapter 负责协议转换，ChannelProcessor/Gateway 继续负责去重、会话、运行时注入、执行和保存。

**边界**：v1 只实现 Web 这个 channel，不做通用多平台配置中心，也不重构现有 Local channel。需要改的是 Gateway 支持显式 session_id，而不是 Web 自己直接跑 runner。

```python
class WebTransport:
    """HTTP/SSE 请求入口。FastAPI router 调用它，不直接调用 Runner。"""

    def to_event(self, request: ChatStreamRequest) -> PlatformEvent:
        return PlatformEvent(
            platform="web",
            transport="http-sse",
            event_id=request.message_id,
            received_at=now_ms(),
            payload={
                "session_id": request.session_id,
                "text": request.text,
            },
        )


class WebAdapter:
    """将 Web PlatformEvent 转成标准 InboundMessage。"""

    def to_inbound_message(self, event: PlatformEvent) -> InboundMessage:
        payload = event.payload
        return InboundMessage(
            channel="web",
            account_id="default",
            peer_id="web",
            sender_id="web",
            message_id=event.event_id,
            text=payload["text"],
            timestamp=event.received_at,
            message_type="text",
            raw=payload,
            metadata={"session_id": payload["session_id"]},
        )
```

Gateway 增加显式 session_id 路由：

```python
async def _resolve_session(self, message: InboundMessage) -> Session:
    session_id = message.metadata.get("session_id")
    if session_id:
        session = await self._session_store.get_by_id(session_id)
        if session is None:
            raise SessionNotFound(session_id)
        # 防御：显式 session_id 的消息不能自动切 active session
        return session
    return await self._get_or_create_session(message)
```

**关键设计**：
- Web router 只负责 HTTP/SSE 编解码，不直接接触 `session_store`、`agent_runner`
- Web 请求通过 `WebTransport → WebAdapter → ChannelProcessor.process_stream → Gateway.handle_stream`
- Gateway 识别 `message.metadata["session_id"]` 后按 session_id 加载会话；没有 session_id 时仍按 peer active session 兼容 CLI
- 所有 RuntimeProfile、AgentRuntime、memory、skills、工具、model、chunk 聚合、保存和 delivery 仍在 Gateway/Runner 链路内统一处理
- 自动压缩是否开启由 Gateway 参数/策略控制，不在 Web 层复制逻辑
- Web channel 的测试要覆盖 adapter 转换、processor 去重、gateway 显式 session_id 路由、SSE chunk 转发四段链路

### 6. 对话创建 API — 通过 Gateway 创建绑定 agent_id 的 Session

**问题**：现有 `RuntimeGateway.create_new_session` 固定使用 `_default_agent_id`，如果直接复用，所有 Web 会话都绑到 `default-agent`。

**方案**：Gateway 增加公开 `create_session_for_agent(message, agent_id)`，Web 仍通过 WebTransport/WebAdapter 构造标准 message 后调用 Gateway，不直接调用底层 `create_session`。

```python
async def create_session_for_agent(
    self,
    message: InboundMessage,
    agent_id: str,
) -> Session:
    agent = self._agent_resolver.resolve(agent_id)
    session = create_session(message, agent_id=agent.agent_id)
    await self._session_store.save(session)
    await self._session_store.set_active(self._peer_key(message), session.session_id)
    return session
```

Web Conversation API：
```python
async def create_conversation(request: CreateConversationRequest) -> SessionSchema:
    event = web_transport.to_create_conversation_event(request)
    message = web_adapter.to_inbound_message(event)
    session = await gateway.create_session_for_agent(message, request.agent_id)
    return SessionSchema.from_session(session)
```

列表/详情/删除也优先走 Gateway 公开方法：
```python
async def list_conversations(message: InboundMessage) -> list[Session]:
    return await gateway.list_sessions(message)

async def get_conversation(session_id: str) -> Session | None:
    return await gateway.get_session_by_id(session_id)

async def delete_conversation(message: InboundMessage, session_id: str) -> None:
    await gateway.delete_session(message, session_id)
```

### 7. Expert install/uninstall 安全校验

**install-from-file 安全校验**：
```python
class ExpertMarketplace:
    def install_from_file(self, file_path: str | Path) -> Expert:
        path = Path(file_path).resolve()
        # 1. 路径穿越校验：限制在允许的目录内
        if not str(path).startswith(str(ALLOWED_DIRS)):
            raise ValueError(f"Path outside allowed directories: {path}")
        # 2. 读取并解析 EXPERT.md
        expert = self._store.parse_expert_md(path)
        # 3. 名称格式校验：仅允许小写字母数字+连字符
        if not re.match(r'^[a-z][a-z0-9-]*$', expert.name):
            raise ValueError(f"Invalid expert name: {expert.name}")
        # 4. 冲突检查：bundled 专家不允许覆盖
        if self._store.is_bundled(expert.name):
            raise ValueError(f"Cannot overwrite bundled expert: {expert.name}")
        # 5. 写入 SQLite experts 表；source_path 记录原始文件路径
        ...
```

**DELETE expert 安全校验**：
```python
    def uninstall(self, expert_name: str) -> None:
        expert = self._store.load(expert_name)
        # 禁止删除 bundled 专家
        if expert.source == "bundled":
            raise ValueError(f"Cannot uninstall bundled expert: {expert_name}")
        # 检查是否有 Agent 实例依赖此专家（可选警告）
        ...
```

### 8. SSE 流式实现

后端用 `sse-starlette` 的 `EventSourceResponse`，前端用 `fetch` + `ReadableStream`（支持 POST body，不使用 EventSource）。

### 9. Web 层 peer_key

单用户场景先用固定 peer 路由字段：`channel=web, account_id=default, peer_id=web`，因此 peer_key 为 `web:default:web`。这只用于“列出当前 Web 用户的会话”和无显式 session_id 的兼容路径。

聊天请求必须带 `session_id`，Gateway 优先用 `message.metadata["session_id"]` 精确加载会话，不依赖 active session。未来加多用户时，把 `account_id/peer_id` 替换成真实用户/租户 ID 即可。

### 10. CLI 向后兼容

- CLI 启动时创建或加载 `default-agent`，并把 `AgentResolver` 注入 Gateway
- 旧的 `default_agent_id="default-agent"` 仍保留，用于创建 CLI Session
- 如果测试或嵌入场景不传 `agent_resolver`，Gateway 回退到旧行为：不注入 RuntimeProfile，只使用初始化时传入的 runner/tools/skills/model
- 因此 CLI 默认行为保持可用，但默认 Agent 配置修改后可在下一轮生效

---

## 实现阶段

### V1 Phase 1: SQLite 基础 + Expert 核心模型 + 内置专家

**目标**：完整的专家模板子系统，独立于其他模块。

**新建文件**：
- `claw/storage/__init__.py`
- `claw/storage/sqlite.py` — SQLite 连接、schema migration、事务 helper、WAL 初始化
- `claw/expert/__init__.py`
- `claw/expert/types.py` — Expert, ExpertMeta 数据类
- `claw/expert/store.py` — SqliteExpertStore（EXPERT.md 导入/导出 + SQLite 持久化）
- `claw/expert/service.py` — ExpertService（CRUD、安装/卸载、冲突/来源规则）
- `claw/expert/registry.py` — ExpertRegistry（查询/搜索/列出）
- `claw/expert/marketplace.py` — install/uninstall 操作
- `claw/expert/bundled/general-assistant/EXPERT.md`
- `claw/expert/bundled/code-helper/EXPERT.md`
- `claw/expert/bundled/paper-reader/EXPERT.md`

**测试**：`tests/test_expert_types.py`, `test_expert_store.py`, `test_expert_service.py`, `test_expert_registry.py`, `test_expert_marketplace.py`

**边界**：Expert 模块不了解 Session/Gateway；专家 CRUD 的业务规则在 `claw/expert/service.py`，Web router 只是调用它。

### V1 Phase 2: Agent 运行实例模型 + 存储 + 工厂

**目标**：Agent 子系统，从 Expert 模板创建可变实例，并提供默认 Agent 配置。

**新建文件**：
- `claw/agent_runtime/__init__.py`
- `claw/agent_runtime/types.py` — AgentConfig 数据类
- `claw/agent_runtime/store.py` — SqliteAgentStore
- `claw/agent_runtime/factory.py` — AgentFactory（Expert → Agent）
- `claw/agent_runtime/resolver.py` — AgentResolver / RuntimeProfile

**测试**：`tests/test_agent_runtime_types.py`, `test_agent_runtime_store.py`, `test_agent_runtime_factory.py`, `test_agent_runtime_resolver.py`

**依赖**：Phase 1 的 Expert 类型

**边界**：Agent 运行时依赖 Expert 类型，但不依赖 Session、Gateway、DeepSeekRunner。

### V1 Phase 3a: Session→Agent 布线 + system_prompt 注入

**目标**：将 `session.agent_id` 连接到 AgentResolver，运行时注入 Agent system prompt。

**修改文件**：
- `claw/ports.py` — 添加 AgentStore / AgentResolver / RuntimeProfile Protocol
- `claw/gateway.py` — 添加 `agent_resolver` 依赖、`_inject_agent_runtime_profile` 方法、显式 `session_id` 路由，以及 `create_session_for_agent` 公开入口
- `claw/deepseek.py` — `_build_messages` 注入 profile.system_prompt

**测试**：`tests/test_agent_runtime_injection.py`

**依赖**：Phase 2 的 AgentStore

**关键**：Session 无需修改（已有 `agent_id` 字段）。默认 CLI Session 使用 `default-agent`，Web Session 使用创建对话时传入的 Agent ID。

### V1 Phase 3b: 动态 model_config

**目标**：DeepSeekAgentRunner 每轮按 RuntimeProfile 的 model_config 构建调用参数。

**修改文件**：
- `claw/deepseek.py` — `run/run_stream/_build_kwargs` 支持本轮 model/temperature 覆盖

**测试**：`tests/test_agent_runtime_model.py`

**依赖**：V1 Phase 3a

### V1 Phase 3c: tools / skills / MCP 按 Agent 配置过滤

**目标**：不创建 per-Agent runtime 实例，只在本轮请求中按 AgentConfig 过滤可见能力。

**修改文件**：
- `claw/deepseek.py` — 本轮 tools schema 按 enabled_tools / enabled_mcp_servers 过滤
- `claw/tools.py` — 增加只读过滤方法，不修改全局 registry
- `claw/skills/registry.py` — skills listing 支持 enabled_skills 参数
- `claw/mcp/bridge.py` — 明确 MCP 工具命名空间过滤规则

**测试**：`tests/test_agent_runtime_tools.py`, `tests/test_agent_runtime_skills.py`, `tests/test_agent_runtime_mcp.py`

**依赖**：V1 Phase 3a

### V1 Phase 4: FastAPI 基础 + Web Channel 骨架 + 专家 API

**目标**：FastAPI Web 服务器 + WebTransport/WebAdapter 骨架 + 专家 CRUD API。

**修改文件**：
- `pyproject.toml` — 添加 `fastapi>=0.115.0`, `uvicorn>=0.34.0`, `sse-starlette>=2.0.0`
- `claw/storage/sqlite.py` — 初始化 SQLite schema（如果 Phase 1 未完成）

**新建文件**：
- `web/__init__.py`, `web/app.py`, `web/deps.py`
- `web/routers/__init__.py`, `web/routers/experts.py`
- `web/schemas/__init__.py`, `web/schemas/expert.py`
- `web/channel.py` — WebTransport / WebAdapter 骨架

**测试**：`tests/test_web_experts_api.py`, `tests/test_web_channel.py`

**依赖**：Phase 1

### V1 Phase 5: Agent API + 对话 API

**目标**：Agent CRUD + Session 创建/管理端点。

**新建文件**：
- `web/routers/agents.py`
- `web/routers/conversations.py`
- `web/schemas/agent.py`
- `web/schemas/conversation.py`

**测试**：`tests/test_web_agents_api.py`, `tests/test_web_conversations_api.py`

**依赖**：Phase 2, Phase 3, Phase 4

### V1 Phase 6: 聊天流式 API

**目标**：SSE 流式聊天端点。

**新建文件**：
- `web/routers/chat.py`
- `web/schemas/chat.py`

**测试**：`tests/test_web_chat_api.py`（用 EchoAgentRunner 避免 API 调用）

**依赖**：Phase 3, Phase 5

### V1 Phase 7: React 前端

**目标**：Web UI — 专家广场、对话列表、流式聊天。

**新建文件**：`web/frontend/` 下 React + Vite + TailwindCSS 项目

**核心组件**：Layout, Sidebar, ExpertMarketplace, ConversationList, ChatWindow, MessageBubble

**SSE Hook**：`useSSE.ts` 用 `fetch` + `ReadableStream`（POST body）

**测试**：手动验证完整流程

**依赖**：Phase 4, Phase 5, Phase 6

### V1 Phase 8: 集成测试 + 文档 + Review

**目标**：端到端集成测试 + 文档更新 + codex:adversarial-review 闭环。

**新建文件**：
- `tests/test_expert_agent_session_flow.py` — 跨三层集成测试

**修改文件**：
- `docs/plans/2026-05-18-expert-square-web.md` — 同步更新
- `CLAUDE.md` — 添加 expert/agent_runtime 文档

**测试场景**：
1. 安装 Expert → 验证 EXPERT.md 存在
2. 从 Expert 创建 Agent → 验证字段复制
3. 创建 Session(agent_id) → 验证绑定
4. 发消息 → 验证 RuntimeProfile 注入
5. 修改 Agent system_prompt → 验证下次消息使用新 prompt
6. 修改 Agent model_config → 验证下次消息使用新 model/temperature
7. 修改 Agent enabled_tools → 验证只暴露允许工具
8. 修改 Agent enabled_skills → 验证 skills listing 只包含允许技能
9. 修改 Agent enabled_mcp_servers → 验证只暴露允许 MCP server 的工具
10. 删除 Agent → 验证已有 Session 回退 default-agent 并标记 agent_missing
11. 回归测试：CLI 模式通过 default-agent 运行，不受 Web Agent 影响

---

## V2 演进阶段

### V2 Phase 1: AgentRuntimeManager 深隔离

**目标**：从 V1 的“全局 registry + 过滤”升级为每个 Agent 独立 runtime。

**新建/修改文件**：
- `claw/agent_runtime/runtime.py` — AgentRuntimeManager / AgentRuntime
- `claw/tools.py` — 支持从 builtin catalog 注册指定工具到指定 registry
- `claw/skills/registry.py` — 支持从 SkillStore 加载指定 skills 到指定 registry
- `claw/mcp/manager.py` / `claw/mcp/bridge.py` — 支持只连接/注册指定 MCP servers 到 Agent 专属 registry

**测试**：
- `tests/test_agent_runtime_manager.py`
- `tests/test_agent_runtime_isolation.py`
- `tests/test_agent_runtime_mcp_lifecycle.py`

### V2 Phase 2: Agent runtime cache / invalidation

**目标**：Agent 配置更新后，准确失效该 Agent 的 runtime cache，避免旧 tools/skills/MCP 配置继续生效。

**测试**：
- 更新 Agent tools 后下一轮立即生效
- 更新 MCP server 列表后重建连接
- 多 Agent 并发互不影响

### V2 Phase 3: EXPERT.md 导出与版本管理

**目标**：在 V1 仅支持 EXPERT.md 导入的基础上，增加 SQLite → EXPERT.md 导出和版本元数据管理。

**测试**：
- SQLite Expert 导出 EXPERT.md
- 导出后再导入字段一致
- bundled / local / exported 来源标记正确

### V2 Phase 4: 多平台 channel 管理

**目标**：在 V1 Web channel 骨架稳定后，抽象多平台 channel 配置和认证。

**范围**：
- 多用户 / 多租户 `account_id`、`peer_id`
- Web / CLI / 后续 Slack/Feishu 等 channel 的统一配置
- channel-level auth / rate limit / audit

---

## 依赖关系图（DAG）

```
V1 Phase 1 (SQLite + Expert 核心)
  ├──→ V1 Phase 2 (Agent 存储 + Resolver)
  │       └──→ V1 Phase 3a (prompt 注入)
  │               ├──→ V1 Phase 3b (model_config)
  │               ├──→ V1 Phase 3c (tools/skills/MCP 过滤)
  │               └──→ V1 Phase 5 (Agent + Conversation API) ←── V1 Phase 4
  │                       └──→ V1 Phase 6 (Chat SSE API)
  │                               └──→ V1 Phase 7 (React 前端)
  │                                       └──→ V1 Phase 8 (集成 + 文档)
  └──→ V1 Phase 4 (FastAPI + Web Channel + Expert API)

V2 Phase 1 (per-Agent runtime) ←── V1 Phase 3c
  └──→ V2 Phase 2 (runtime cache/invalidation)
          ├──→ V2 Phase 3 (EXPERT.md 导出)
          └──→ V2 Phase 4 (多平台 channel 管理)
```

**并行机会**：
- V1 Phase 1 完成后，V1 Phase 2 和 V1 Phase 4 可以并行
- V1 Phase 3a 完成后，V1 Phase 3b/3c 可以并行
- V1 Phase 5 需要 V1 Phase 2/3a 和 V1 Phase 4
- V1 Phase 6 需要 V1 Phase 3a + V1 Phase 5
- V2 不阻塞 V1 发布

---

## 验证方案

1. **单元测试**：每个 Phase 完成后运行 `uv run pytest tests/`
2. **API 测试**：FastAPI TestClient 测试所有端点
3. **流式测试**：验证 SSE 事件序列（thinking → content → tool_call → tool_result → DONE）
4. **集成验证**：启动 uvicorn + vite dev，浏览器走通：选专家 → 创建 Agent → 创建对话 → 发消息 → 流式响应
5. **V1 动态配置验证**：同一进程内创建两个 Agent，分别配置不同 prompt/model/tools/skills/MCP 过滤列表，验证两条 Session 互不串配置
6. **默认 Agent 回归**：未选择 Agent 的 CLI 会话使用 `default-agent`，且 default-agent 修改后下一轮生效
7. **SQLite 存储验证**：migration 幂等、事务写入、SessionStore contract、消息历史恢复、删除 cascade
8. **专家 CRUD 验证**：Web API 调 ExpertService，覆盖 create/update/delete/list/search、bundled 不可删、local 覆盖确认
9. **Web channel 验证**：FastAPI router → WebTransport → WebAdapter → ChannelProcessor → Gateway → Runner 全链路走通，且显式 `session_id` 不串 active session
10. **回归测试**：`uv run pytest tests/` 全量通过，CLI 模式不受影响
11. **V2 隔离验证**：per-Agent runtime 启用后，验证独立 registry/MCP 连接/cache invalidation
12. **代码闭环**：实现完成后 `codex:adversarial-review` 审查，修复后再次审查直到通过

---

## 关键设计决策

1. **`claw/agent_runtime/` 而非 `claw/agent/`**：`claw/agent.py` 已存在（MiniClaw facade），新目录避免破坏现有导入
2. **Session 不加 `expert_id`**：Session 通过 `agent_id` 间接关联 Expert，`AgentConfig.source_expert` 记录来源
3. **默认 Agent 是真实配置**：CLI/兼容路径使用 `default-agent`，但 default-agent 存在于 AgentStore 中，和普通 Agent 走同一套 resolver/runtime profile
4. **RuntimeProfile 存 metadata**：遵循 memory_context、skills_listing 的现有模式，每轮从 `session.agent_id` 重新解析配置，确保 Agent 修改后下一轮立即生效
5. **SQLite 作为 v1 统一存储**：Expert、Agent、Session、Message 先统一存 `data/mini_claw.sqlite`；EXPERT.md 保留为导入/导出和 bundled seed 格式
6. **CLI 完全兼容**：CLI 默认注入 `default-agent` 的 resolver；测试/嵌入场景不注入 resolver 时，Gateway 回退旧行为
7. **Web 是 v1 正式 channel**：Web 通过 `WebTransport → WebAdapter → ChannelProcessor → Gateway` 进入系统；Gateway 支持显式 `session_id`，避免多对话/多标签页串台。v1 只新增 Web channel，不做通用多平台配置中心
8. **对话创建支持传入 agent_id**：Web 通过 Gateway 公开 `create_session_for_agent(message, agent_id)` 创建会话，不直接调用底层 `create_session`
9. **专家 CRUD 入口在 Web，规则在 core**：Web router 提供专家管理界面/API；`claw/expert/service.py` 负责业务规则，`claw/expert/store.py` 负责 SQLite
10. **Expert install/uninstall 安全校验**：路径穿越检查、名称格式校验、bundled 专家不可覆盖/删除、local 覆盖需显式确认
11. **V1 能力控制用过滤实现**：tools/skills/MCP 先用全局 registry + Agent 配置过滤，避免第一版引入 per-Agent MCP 连接和缓存失效复杂度
12. **工具/技能/MCP 默认拒绝**：V1 空列表表示禁用，不支持 `["*"]`；默认 Agent 显式列出允许项

---

## 关键参考文件

- `claw/storage/sqlite.py` — SQLite schema、migration、事务 helper
- `claw/skills/store.py` — ExpertStore 的 EXPERT.md 解析参考
- `claw/gateway.py` — 需添加 agent_resolver + RuntimeProfile 注入方法
- `claw/deepseek.py` — V1 需注入 agent system prompt，并按 profile 动态设置 model / 过滤 tools
- `claw/types.py` — Session 已有 `agent_id` 字段（无需修改）
- `claw/ports.py` — 需添加 AgentStore / AgentResolver / RuntimeProfile 相关 Protocol
- `claw/tools.py` — V1 需支持按 enabled_tools / enabled_mcp_servers 生成本轮 tools schema
- `claw/skills/registry.py` — V1 需支持按 enabled_skills 生成本轮 skills listing
- `claw/agent_runtime/runtime.py` — V2 需按 Agent 配置安装/注册 skills、MCP、tools
