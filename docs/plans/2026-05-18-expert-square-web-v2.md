# 专家广场 Web UI V2 演进方案 — Agent Runtime 深隔离 + 存储统一 + 多平台扩展

## Context

V1 建立了 Expert → Agent → Session 三层架构、SQLite 统一存储（Expert/Agent）、Web channel 抽象、Agent 动态 system_prompt/model_config、tools/skills/MCP 按 Agent 配置过滤。V1 已完整闭环，但存在以下限制：

1. **tools/skills/MCP 仍在全局 registry 上过滤**：所有 Agent 共享同一个 ToolsRegistry、SkillsRegistry、McpManager，通过 enabled_* 列表做运行时过滤。多个 Agent 并发请求时，MCP 连接是全局共享的，无法按 Agent 隔离连接和工具。
2. **Session 存储分裂**：CLI 用 JsonlSessionStore，Web 用 SqliteSessionStore，两套代码路径长期共存增加维护成本。
3. **EXPERT.md 只进不出**：V1 只支持 EXPERT.md → SQLite 导入，不支持导出，bundled experts 用 Python 定义。
4. **单用户场景**：Web peer_key 固定 `web:default:web`，无多用户/多租户能力。

V2 的核心目标：**Agent 运行时能力真正隔离 + 存储路径统一 + 为多平台扩展打下基础**。

---

## V2 前置条件

V2 在 V1 全部 Phase 完成并稳定后启动。以下 V1 成果是 V2 的依赖：

| V1 成果 | V2 依赖方式 |
|---------|------------|
| `claw/agent_runtime/types.py` AgentConfig | V2 扩展 AgentConfig 不变，新增运行时缓存字段 |
| `claw/agent_runtime/resolver.py` AgentResolver / RuntimeProfile | V2 的 AgentRuntimeManager 消费 RuntimeProfile |
| `claw/gateway.py` agent_resolver + _inject_agent_runtime_profile | V2 替换注入逻辑：从全局过滤 → per-Agent runtime |
| `claw/deepseek.py` model_config 动态 + tools 过滤 | V2 改为接收 per-Agent ToolsRegistry |
| `claw/storage/sqlite.py` | V2 新增 sessions/messages 表的 CLI 读写路径 |
| `claw/tools.py` ToolsRegistry | V2 新增 `clone()` 或工厂方法 |
| `claw/skills/registry.py` SkillsRegistry | V2 新增按 SkillStore 子集加载 |
| `claw/mcp/manager.py` McpManager | V2 新增 per-Agent McpManager 实例 |
| `web/channel.py` WebTransport/WebAdapter | V2 扩展多平台时复用 |

---

## V2 范围

### 包含

- **Phase 1**: AgentRuntimeManager — per-Agent ToolsRegistry / SkillsRegistry / McpManager
- **Phase 2**: Agent Runtime 缓存与失效机制
- **Phase 3**: Session 存储统一 — CLI 从 JSONL 迁移到 SQLite
- **Phase 4**: EXPERT.md 导出与版本管理
- **Phase 5**: 多平台 Channel 管理框架
- **Phase 6**: 工具/技能/MCP 审计日志

### 不包含

- 多模态支持（图片/文件上传）
- Agent 间协作/路由（一个 Agent 调另一个 Agent）
- 前端大规模重构（V2 前端只补充管理界面）
- 分布式部署支持

---

## Phase 1: AgentRuntimeManager — per-Agent Runtime 隔离

### 目标

从 V1 的"全局 registry + 过滤"升级为每个 Agent 独立的运行时实例。每个 Agent 拥有自己的 ToolsRegistry、SkillsRegistry、McpManager，Agent 配置更新只影响自己的 runtime。

### 数据模型

```python
# claw/agent_runtime/runtime.py

@dataclass(slots=True)
class AgentRuntime:
    """单个 Agent 的运行时：独立注册表、独立 MCP 连接。"""
    agent_id: str
    tools_registry: ToolsRegistry
    skills_registry: SkillsRegistry
    mcp_manager: McpManager | None
    config_hash: str                  # AgentConfig 关键字段的 hash，用于缓存失效

class AgentRuntimeManager:
    """按 Agent 配置构建和管理 AgentRuntime 实例。"""

    def __init__(
        self,
        *,
        builtin_tools: BuiltinToolCatalog,
        skill_store: SkillStore,
        mcp_config_store: McpConfigStore,
    ) -> None:
        self._builtin_tools = builtin_tools
        self._skill_store = skill_store
        self._mcp_config_store = mcp_config_store
        self._runtimes: dict[str, AgentRuntime] = {}   # agent_id → AgentRuntime

    async def get_runtime(self, profile: RuntimeProfile) -> AgentRuntime:
        """获取或构建 Agent 的运行时。配置未变时复用缓存。"""
        ...

    async def invalidate(self, agent_id: str) -> None:
        """显式失效指定 Agent 的 runtime 缓存。"""
        ...

    async def shutdown(self) -> None:
        """关闭所有 Agent runtime，断开 MCP 连接。"""
        ...
```

### BuiltinToolCatalog — 内置工具目录

V2 需要一个内置工具的"目录"，不直接注册到全局 registry，而是按 Agent 配置选择性地注册到 Agent 专属 registry。

```python
# claw/builtin_tools/catalog.py

class BuiltinToolCatalog:
    """内置工具目录：持有所有可用内置工具的定义，按需注册到指定 registry。"""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register_definition(self, definition: ToolDefinition) -> None:
        ...

    def available_tools(self) -> list[str]:
        """返回所有可用内置工具名。"""
        ...

    def register_to(self, tool_names: list[str], registry: ToolsRegistry) -> None:
        """将指定内置工具注册到目标 registry。"""
        ...

@dataclass(slots=True)
class ToolDefinition:
    """工具定义：不含 handler，注册时绑定 handler。"""
    name: str
    description: str
    parameters: dict[str, Any] | None
    handler_factory: Callable[[], ToolHandler]
```

### SkillsRegistry 按需加载

V1 的 SkillsRegistry 从全局 SkillStore 加载所有 skills。V2 需要支持按 Agent 的 enabled_skills 只加载指定 skills 到 Agent 专属 SkillsRegistry。

```python
# claw/skills/registry.py 扩展

class SkillsRegistry:
    # 现有方法不变

    @classmethod
    def from_store_subset(
        cls,
        store: SkillStore,
        skill_names: list[str],
    ) -> SkillsRegistry:
        """从 SkillStore 中只加载指定 skills 创建新 registry。"""
        registry = cls()
        for name in skill_names:
            skill = store.load(name)
            if skill:
                registry.register(skill)
        return registry
```

### McpManager per-Agent 实例

V1 的 McpManager 连接所有配置的 MCP servers。V2 每Agent 拥有独立的 McpManager，只连接 enabled_mcp_servers 列表中的 server。

```python
# claw/agent_runtime/runtime.py 内部构建逻辑

async def _build_mcp_manager(self, profile: RuntimeProfile) -> McpManager | None:
    """按 Agent 配置创建专属 McpManager，只连接 enabled_mcp_servers。"""
    if not profile.enabled_mcp_servers:
        return None
    configs = self._mcp_config_store.get_by_names(profile.enabled_mcp_servers)
    manager = McpManager(configs)
    await manager.start()
    return manager
```

### AgentRuntime 构建流程

```
AgentRuntimeManager.get_runtime(profile)
  ├── 检查缓存：agent_id + config_hash 是否命中
  │   ├── 命中 → 返回缓存的 AgentRuntime
  │   └── 未命中 → 继续构建
  ├── 创建空 ToolsRegistry
  ├── BuiltinToolCatalog.register_to(profile.enabled_tools, tools_registry)
  ├── SkillsRegistry.from_store_subset(skill_store, profile.enabled_skills)
  ├── McpManager(configs=enabled_mcp_servers) → register_tools(tools_registry)
  ├── 计算 config_hash
  ├── 缓存 AgentRuntime
  └── 返回
```

### Gateway 集成变更

V2 替换 V1 的过滤注入逻辑：

```python
# V1（全局过滤）
profile = agent_resolver.resolve(session.agent_id)
session.metadata["agent_runtime_profile"] = profile
session.metadata["skills_listing"] = skills_registry.build_skills_listing(
    enabled_skills=profile.enabled_skills,
)
runner.run_stream(session, message, enabled_tools=profile.enabled_tools, ...)

# V2（per-Agent runtime）
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

### deepseek.py 变更

V2 的 `DeepSeekAgentRunner` 接收 per-Agent ToolsRegistry：

```python
# V2 run_stream 签名扩展
async def run_stream(
    self,
    session: Session,
    message: InboundMessage,
    *,
    model_config: dict[str, Any] | None = None,
    tools_registry: ToolsRegistry | None = None,     # V2 新增
) -> AsyncIterator[StreamChunk]:
    # 如果传入 tools_registry，使用它；否则回退到 self._tools_registry
    registry = tools_registry or self._tools_registry
    ...
```

### Agent 配置更新触发失效

```python
# web/routers/agents.py 或 claw/agent_runtime/service.py

async def update_agent(self, agent_id: str, updates: dict) -> AgentConfig:
    agent = self._agent_store.get(agent_id)
    # 应用更新...
    self._agent_store.save(agent)
    # 失效 runtime 缓存
    await self._agent_runtime_manager.invalidate(agent_id)
    return agent
```

### 测试

- `tests/test_agent_runtime_manager.py` — 构建/缓存/失效基本流程
- `tests/test_agent_runtime_isolation.py` — 两个 Agent 使用不同 tools/skills/MCP，互不影响
- `tests/test_agent_runtime_mcp_lifecycle.py` — per-Agent MCP 连接建立/断开/重连
- `tests/test_agent_runtime_config_update.py` — 更新 Agent 配置后 runtime 立即重建
- `tests/test_builtin_tool_catalog.py` — 按名称注册到指定 registry
- 回归：V1 的所有测试仍然通过（V2 替换内部实现，不改变外部 contract）

---

## Phase 2: Agent Runtime 缓存与失效

### 目标

Agent 配置更新后，准确失效该 Agent 的 runtime 缓存，避免旧 tools/skills/MCP 配置继续生效。同时避免每轮请求都重建 runtime（尤其是 MCP 连接开销大）。

### 缓存策略

```python
def _config_hash(self, profile: RuntimeProfile) -> str:
    """计算 Agent 配置的 hash，用于缓存命中判断。"""
    key = json.dumps({
        "enabled_tools": sorted(profile.enabled_tools),
        "enabled_skills": sorted(profile.enabled_skills),
        "enabled_mcp_servers": sorted(profile.enabled_mcp_servers),
    }, sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:16]
```

### 失效时机

| 事件 | 失效方式 |
|------|---------|
| Agent 配置更新（API PUT） | 显式调用 `invalidate(agent_id)` |
| Agent 删除 | 清理 runtime + 断开 MCP |
| MCP server 配置变更 | 失效所有使用该 server 的 Agent runtime |
| Skill 文件更新 | 失效所有启用该 skill 的 Agent runtime |

### 缓存清理

```python
class AgentRuntimeManager:
    async def invalidate(self, agent_id: str) -> None:
        runtime = self._runtimes.pop(agent_id, None)
        if runtime and runtime.mcp_manager:
            await runtime.mcp_manager.stop()

    async def shutdown(self) -> None:
        for runtime in self._runtimes.values():
            if runtime.mcp_manager:
                await runtime.mcp_manager.stop()
        self._runtimes.clear()
```

### 测试

- 配置不变时第二次 get_runtime 命中缓存
- enabled_tools 增减后 config_hash 变化，缓存失效
- invalidate 后 MCP 连接断开
- 多 Agent 并发 get_runtime 无竞态
- 长时间运行后 runtime 数量不泄漏

---

## Phase 3: Session 存储统一 — CLI 迁移到 SQLite

### 目标

消除 JsonlSessionStore 和 SqliteSessionStore 两套代码路径。CLI 和 Web 统一使用 SqliteSessionStore。JsonlSessionStore 降级为测试用（内存模式保留，文件模式可选）。

### 迁移路径

```
V1: CLI → JsonlSessionStore (data/sessions/)  |  Web → SqliteSessionStore (data/mini_claw.sqlite)
V2: CLI → SqliteSessionStore (data/mini_claw.sqlite)  |  Web → SqliteSessionStore (同上)
```

### 迁移步骤

1. **`claw/agent.py`（MiniClaw facade）修改**：将 SessionStore 初始化从 JsonlSessionStore 切换到 SqliteSessionStore
2. **数据迁移工具** `scripts/migrate_sessions_to_sqlite.py`：
   - 读取 `data/sessions/index.json` + `data/sessions/*.jsonl`
   - 写入 `data/mini_claw.sqlite` 的 sessions / session_messages / active_sessions 表
   - 幂等：已迁移的 session 跳过
3. **`chat/app.py` 修改**：使用 SqliteSessionStore
4. **JsonlSessionStore 保留**：`InMemorySessionStore` 继续用于测试；`JsonlSessionStore` 标记 deprecated

### 迁移工具设计

```python
# scripts/migrate_sessions_to_sqlite.py

def migrate(jsonl_dir: Path, sqlite_path: Path) -> MigrationResult:
    """将 JSONL session 数据迁移到 SQLite。

    幂等操作：已存在的 session 跳过。
    """
    sqlite_store = SqliteSessionStore(sqlite_path)
    index = json.loads((jsonl_dir / "index.json").read_text())

    migrated = 0
    skipped = 0
    for peer_key, entry in index.items():
        for session_id, meta in entry["sessions"].items():
            # 检查是否已迁移
            existing = await sqlite_store.get_by_id(session_id)
            if existing:
                skipped += 1
                continue
            # 从 JSONL 读取 history
            history = read_jsonl_history(jsonl_dir / f"{session_id}.jsonl", meta)
            session = build_session_from_meta(session_id, peer_key, meta, history)
            await sqlite_store.save(session)
            migrated += 1

    return MigrationResult(migrated=migrated, skipped=skipped)
```

### 测试

- 迁移前后 Session 数据一致（metadata、history、summary、history_offset）
- 迁移后 active_session 映射正确
- 空 JSONL 目录不报错
- 已迁移数据再次迁移跳过
- CLI 全量回归测试通过

---

## Phase 4: EXPERT.md 导出与版本管理

### 目标

在 V1 只支持 EXPERT.md → SQLite 导入的基础上，增加 SQLite → EXPERT.md 导出，支持专家模板的版本管理和分享。

### 导出格式

复用 V1 的 EXPERT.md 格式（YAML frontmatter + Markdown body）：

```python
# claw/expert/store.py 扩展

def export_to_expert_md(self, expert_name: str, output_dir: Path) -> Path:
    """将 SQLite 中的 Expert 导出到 EXPERT.md 文件。"""
    expert = self.load(expert_name)
    if expert is None:
        raise ValueError(f"Expert not found: {expert_name}")

    expert_dir = output_dir / expert.name
    expert_dir.mkdir(parents=True, exist_ok=True)
    path = expert_dir / "EXPERT.md"

    frontmatter = {
        "name": expert.name,
        "display_name": expert.display_name,
        "description": expert.description,
        "default_skills": expert.default_skills,
        "default_tools": expert.default_tools,
        "default_mcp_servers": expert.default_mcp_servers,
        "default_model": expert.default_model,
        "default_memory": expert.default_memory,
        "default_sandbox": expert.default_sandbox,
        "meta": {
            "version": expert.meta.version,
            "author": expert.meta.author,
            "tags": expert.meta.tags,
            "category": expert.meta.category,
            "avatar": expert.meta.avatar,
        },
    }

    content = f"---\n{yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)}---\n\n{expert.system_prompt}"
    path.write_text(content, encoding="utf-8")
    return path
```

### 往返一致性测试

```python
def test_round_trip(tmp_path):
    """导入 EXPERT.md → 导出 EXPERT.md → 再次导入，字段一致。"""
    # 1. 导入
    expert = store.import_from_expert_md(bundled_path / "code-helper" / "EXPERT.md")
    # 2. 导出
    exported_path = store.export_to_expert_md(expert.name, tmp_path)
    # 3. 再次导入
    reimported = store.import_from_expert_md(exported_path)
    # 4. 断言
    assert reimported.name == expert.name
    assert reimported.system_prompt == expert.system_prompt
    assert reimported.default_tools == expert.default_tools
    assert reimported.default_skills == expert.default_skills
```

### 版本元数据

Expert 表增加导出相关字段（或使用 meta_json）：

```sql
-- 不需要新列，meta_json 里已有 version/author
-- 导出时写入当前 meta.version，导入时递增 patch version
```

### API 端点扩展

| Method | Path | Description |
|--------|------|-------------|
| GET | `/experts/{name}/export` | 下载 EXPERT.md 文件 |

### 测试

- 导出文件格式合法（YAML frontmatter + body）
- 往返一致性
- bundled / local / exported 来源标记正确
- 导出不存在的 Expert 报错

---

## Phase 5: 多平台 Channel 管理框架

### 目标

在 V1 Web channel 骨架稳定后，抽象出通用 channel 管理能力，支持 Web / CLI / 后续 Slack / Feishu 等 channel 的统一配置、认证、路由。

### Channel 抽象

V1 已有 Transport / Adapter Protocol（`claw/ports.py`）。V2 统一管理多个 channel 实例：

```python
# claw/channels/manager.py

class ChannelManager:
    """管理多个 channel 的注册、启动、停止。"""

    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}

    def register(self, name: str, channel: Channel) -> None:
        ...

    async def start_all(self) -> None:
        """启动所有已注册 channel。"""
        ...

    async def stop_all(self) -> None:
        """停止所有已注册 channel。"""
        ...

@dataclass(slots=True)
class Channel:
    """一个 channel 的完整配置。"""
    name: str                        # "web" | "cli" | "slack" | ...
    transport: Transport
    adapter: Adapter
    config: ChannelConfig

@dataclass(slots=True)
class ChannelConfig:
    """Channel 配置。"""
    enabled: bool = True
    auth_required: bool = False
    rate_limit: int | None = None    # requests per minute
    max_concurrent: int = 10
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 多用户支持

V2 引入用户概念，`account_id` 和 `peer_id` 从固定值升级为真实用户标识：

```python
# web/auth.py

class WebAuthService:
    """Web channel 用户认证。v2 先用简单的 API key，后续可接 OAuth。"""

    def authenticate(self, api_key: str) -> UserInfo | None:
        ...

@dataclass(slots=True)
class UserInfo:
    user_id: str                     # 替换 account_id
    display_name: str
    peer_id: str                     # 替换 peer_id
    roles: list[str] = field(default_factory=list)
```

### Gateway 适配

Gateway 的 `_peer_key` 逻辑不变（`channel:account_id:peer_id`），只是 account_id 和 peer_id 从固定值变为动态值。多用户场景下每个用户有自己的 peer_key，Session 天然隔离。

### 测试

- 多用户创建的 Session 互不可见
- 同一用户在 Web 和 CLI 看到相同的 Agent 列表
- Channel 启停不影响其他 Channel
- rate limit 生效

---

## Phase 6: 工具/技能/MCP 审计日志

### 目标

记录每个 Agent 每轮使用了哪些工具、技能、MCP server，用于用量统计、安全审计、问题排查。

### SQLite 审计表

```sql
CREATE TABLE agent_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    category TEXT NOT NULL,           -- "tool" | "skill" | "mcp"
    item_name TEXT NOT NULL,          -- 工具名/技能名/MCP server 名
    action TEXT NOT NULL,             -- "call" | "load" | "register" | "error"
    duration_ms INTEGER,              -- 执行耗时
    result_summary TEXT,              -- 简短结果描述
    error_message TEXT,
    created_at TEXT NOT NULL
);
```

### 审计记录点

| 事件 | category | action |
|------|----------|--------|
| Runner 调用工具 | tool | call |
| LLM 加载技能 | skill | load |
| MCP server 连接 | mcp | register |
| MCP server 调用 | mcp | call |
| 工具/技能/MCP 调用失败 | * | error |

### API 端点

| Method | Path | Description |
|--------|------|-------------|
| GET | `/agents/{id}/audit` | 查询 Agent 审计日志 |
| GET | `/audit/stats` | 全局用量统计 |

### 测试

- 工具调用产生审计记录
- 技能加载产生审计记录
- MCP 调用产生审计记录
- 按 agent_id / session_id / 时间范围过滤
- 审计记录不影响正常执行性能

---

## 目录结构变更

```
mini-claw/
  claw/
    agent_runtime/
      runtime.py                   # V2 新增 — AgentRuntimeManager / AgentRuntime
    builtin_tools/
      catalog.py                   # V2 新增 — BuiltinToolCatalog
    channels/
      manager.py                   # V2 新增 — ChannelManager
    storage/
      sqlite.py                    # V2 扩展 — sessions/messages 表支持
    web/
      auth.py                      # V2 新增 — WebAuthService
  scripts/
    migrate_sessions_to_sqlite.py  # V2 新增 — JSONL → SQLite 迁移工具
```

---

## 依赖关系图

```
V2 Phase 1 (AgentRuntimeManager)     ←── V1 Phase 3c (全局过滤)
  └──→ V2 Phase 2 (缓存/失效)       ←── V2 Phase 1

V2 Phase 3 (Session SQLite 迁移)     ←── V1 Phase 4 (SqliteSessionStore 已存在)

V2 Phase 4 (EXPERT.md 导出)          ←── V1 Phase 1 (ExpertStore 已存在)

V2 Phase 5 (多平台 Channel 管理)     ←── V1 Phase 4 (Web channel 已存在)
                                    ←── V2 Phase 1 (per-Agent runtime)

V2 Phase 6 (审计日志)               ←── V2 Phase 1 (per-Agent runtime)
```

**并行机会**：
- Phase 1 和 Phase 3/4 可以并行
- Phase 5 依赖 Phase 1，但 Phase 3/4 不依赖 Phase 1
- Phase 6 依赖 Phase 1

---

## 关键设计决策

1. **per-Agent runtime 隔离**：每个 Agent 有独立的 ToolsRegistry / SkillsRegistry / McpManager，多 Agent 并发不串配置
2. **BuiltinToolCatalog 先注册后使用**：内置工具定义集中管理，按 Agent 配置选择性地注册到 Agent 专属 registry
3. **McpManager 按 Agent 实例化**：每个 Agent 只连接自己的 enabled_mcp_servers，连接开销由缓存控制
4. **config_hash 缓存**：不每轮重建 runtime，只在配置变更时失效
5. **Session SQLite 统一**：CLI 和 Web 共用 SqliteSessionStore，JsonlSessionStore 降级为测试用途
6. **迁移工具幂等**：已迁移的 session 跳过，支持多次执行
7. **EXPERT.md 往返一致性**：导入 → 导出 → 导入后字段完全一致
8. **Channel 抽象延后**：V2 Phase 5 才引入 ChannelManager，V1 的 Web channel 先稳定运行
9. **审计日志不阻塞主路径**：写入审计表是 fire-and-forget，失败不影响工具执行

---

## 验证方案

1. **per-Agent 隔离测试**：两个 Agent 分别配置不同 tools/skills/MCP，同时发消息，验证互不影响
2. **缓存失效测试**：更新 Agent 配置后发消息，验证使用新的 tools/skills/MCP
3. **MCP 生命周期测试**：Agent 启用/禁用 MCP server 后连接正确建立/断开
4. **Session 迁移测试**：迁移前后 CLI 对话历史完全一致，JSONL → SQLite 数据无损
5. **EXPERT.md 往返测试**：导入 → 导出 → 导入，字段一致
6. **多用户测试**：两个用户各自的 Session/Agent 列表互不可见
7. **审计完整性**：一次完整对话后，审计表包含所有工具调用、技能加载、MCP 调用记录
8. **性能基准**：per-Agent runtime 不显著增加单轮延迟（缓存命中时 <5ms 开销）
9. **回归测试**：`uv run pytest tests/` 全量通过，V1 功能不受影响

---

## 关键参考文件

| 文件 | V2 角色 |
|------|---------|
| `claw/tools.py` | 需支持 clone() 或工厂方法，用于 per-Agent ToolsRegistry |
| `claw/skills/registry.py` | 需支持 from_store_subset()，按 Agent 配置子集加载 |
| `claw/skills/store.py` | SkillStore 的 load/get 供 SkillsRegistry 调用 |
| `claw/mcp/manager.py` | per-Agent 实例化，只连接 enabled_mcp_servers |
| `claw/mcp/bridge.py` | MCP 工具注册到 per-Agent ToolsRegistry |
| `claw/mcp/config.py` | McpConfigStore 按 server 名查找配置 |
| `claw/agent_runtime/resolver.py` | RuntimeProfile 是 AgentRuntimeManager 的输入 |
| `claw/agent_runtime/store.py` | Agent 配置更新后触发 invalidate |
| `claw/gateway.py` | 替换 V1 过滤逻辑为 V2 per-Agent runtime |
| `claw/deepseek.py` | 接收 per-Agent ToolsRegistry |
| `claw/storage/sqlite.py` | V2 新增 sessions/messages 写入路径 + 审计表 |
| `claw/session.py` | SqliteSessionStore 扩展完整 CRUD |
| `claw/ports.py` | AgentRuntimeManager Protocol 定义 |
