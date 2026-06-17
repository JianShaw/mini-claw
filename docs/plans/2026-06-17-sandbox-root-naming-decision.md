# 决策记录：Agent 沙箱目录命名（为何不完整搬 OpenClaw workspace）

> **状态**：已采纳
> **日期**：2026-06-17
> **取代**：`docs/plans/agent-workspace.md`（早期方案，使用了 "workspace" 命名）
> **影响范围**：`claw/agent_runtime/*`、`claw/builtin_tools/*`、`claw/deepseek.py`、`claw/expert/bundled/*/EXPERT.md`、相关测试

---

## 1. 背景

前期实现了一个"每个 agent 独立的文件操作 / shell 执行目录边界"功能，字段命名为 `workspace_root`，默认路径 `data/workspaces/{agent_id}/`。命名时借用了 OpenClaw 的 "workspace" 概念。

复核时发现：**当前实现只覆盖了 OpenClaw workspace 语义的一小部分，却借用了完整概念的名字**，会造成认知误导。

## 2. OpenClaw workspace 到底是什么

依据 [OpenClaw 官方文档 `docs/concepts/agent-workspace.md`](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/concepts/agent-workspace.md)：

> *"The workspace is the agent's home. It is the only working directory used for file tools and for workspace context. Keep it private and treat it as memory."*

OpenClaw 的 workspace 是 **agent 的"家"**，是一个**语义层概念**，至少包含：

| 要素 | 说明 |
|------|------|
| 文件操作默认 cwd | 工具相对路径基准（**默认 cwd，非硬沙箱**，绝对路径仍能越界） |
| bootstrap 文件契约 | `AGENTS.md` / `SOUL.md` / `USER.md` / `IDENTITY.md` / `TOOLS.md` 等，每个 session 启动加载，定义 agent 行为/人格/用户画像 |
| 记忆载体 | `memory/YYYY-MM-DD.md`（日志）+ `MEMORY.md`（长期） |
| 技能源 | `skills/` 目录是最高优先级 skill 加载点 |
| 持久化单元 | 建议用私有 git 仓备份，支持跨机器迁移 |

## 3. 当前实现实际做了什么

只实现了**一个维度**：把工具的 cwd / 路径边界改成 per-agent 目录。

```
data/workspaces/{agent_id}/   ← 一个空目录
```

数据流：
```
AgentFactory.create()        → 分配目录、mkdir、写回 sandbox_config.workspace_root
AgentResolver._resolve_*()   → 解析成绝对路径，写进 RuntimeProfile
DeepSeekAgentRunner          → 从 profile 取出，作为 _workspace_root 注入工具调用
ToolsRegistry.execute()      → merge 进 args（`_` 前缀 = 运行时上下文，不进 LLM schema）
file_ops / shell / ...       → 用 _workspace_root 作为 cwd 与路径边界
```

## 4. 差距对比

| OpenClaw workspace 要素 | mini-claw 现状 | 差距 |
|------------------------|----------------|------|
| 默认 cwd + 路径边界 | ✅ 有 | 已对齐 |
| bootstrap 文件契约（AGENTS/SOUL/USER...） | ❌ 无 | 由 Expert 模板承担 system_prompt，无 SOUL/USER 概念 |
| 启动时加载 bootstrap 进 prompt | ❌ 无 | Expert 体系已覆盖部分 |
| 记忆载体（memory/ + 工具） | ❌ 无 | 走独立 `memory_config`（向量库） |
| skills/ 作为 skill 源 | ❌ 无 | skill 走独立 registry，不从 workspace 加载 |
| 持久化/迁移（git 备份） | ❌ 无 | 未设计 |
| 多 agent 路由到不同 workspace | 🟡 机制有 | 仅 per-agent_id 目录，无路由配置 |

**覆盖率约 1/7**。

## 5. 决策

### 5.1 不完整搬 OpenClaw workspace

理由：

1. **职责重叠**。mini-claw 已有 `Expert` 模板（system_prompt / 人格）、`memory_config`（向量记忆）、`skills/registry`（技能）。若再引入 OpenClaw 式 workspace 的 bootstrap 文件 + memory 目录 + skill 目录，会与现有体系**职责打架**，造成两套并行的"agent 配置源"。

2. **目标场景不同**。OpenClaw workspace 面向"长期运行、有身份、有记忆、可迁移的常驻 agent"；mini-claw 是轻量测试性 runtime。硬搬整套语义是过度设计。

3. **心智成本**。借完整概念的名字但只实现 1/7，会让后续维护者按 OpenClaw 心智模型来用（期待 bootstrap/memory/skill 都从 workspace 来），却发现啥都没有。

### 5.2 改名为 `sandbox_root`

- 当前实现的本质就是 **"每个 agent 的文件操作 / shell 沙箱根目录"**，用 `sandbox_root` 名实相符。
- 同时与 `sandbox_config`（已有的配置字段）形成一致命名族：`sandbox_config.sandbox_root`。
- 默认路径 `data/workspaces/` 同步改为 `data/sandboxes/`。

### 5.3 未做的事

- **不迁移磁盘上的旧 `data/workspaces/` 目录**。代码层面改默认路径即可；若用户已有 `data/workspaces/` 数据，由其自行迁移（避免破坏存量）。default-agent 的新默认路径为 `data/sandboxes/default-agent`。
- **不实现 OpenClaw 式 bootstrap/memory/skill 加载**。若未来确有需求，应单独评估与 Expert / memory_config / skill registry 的关系，另立 ADR。

## 6. 落地的改名映射

| 旧 | 新 |
|----|-----|
| 标识符 `workspace_root` | `sandbox_root` |
| dict key `"workspace_root"` | `"sandbox_root"` |
| 运行时注入键 `_workspace_root` | `_sandbox_root` |
| 函数 `_ensure_workspace` | `_ensure_sandbox` |
| 函数 `_resolve_workspace_root` | `_resolve_sandbox_root` |
| 常量 `_DEFAULT_WORKSPACE_BASE` | `_DEFAULT_SANDBOX_BASE` |
| 默认路径 `data/workspaces/` | `data/sandboxes/` |
| 注释/docstring/schema 文案中的 "workspace" | "sandbox" |

**未改名（刻意保留）**：
- `claw/skills/bundled/*/SKILL.md` —— 面向用户的通用文案，"workspace" 非本项目专有术语
- `tests/test_mcp_config.py` 的 `MY_WORKSPACE` —— 测试用环境变量名，与本功能无关

## 7. 验证

- `grep -rn -i "workspace_root\|_workspace_root\|workspaces/" claw/ tests/ claw/expert/bundled/` → 无残留（仅 SKILL.md / test_mcp_config.py 例外）
- `uv run pytest tests/test_agent_runtime.py tests/test_builtin_tools.py` → 92 passed, 2 skipped

## 8. 后续指引

- 若将来要引入"agent 身份 + 记忆的物理载体"，**不要复用 `sandbox_root` 这个名字**，另起一个（如 `agent_home`）并写新 ADR 说明与 Expert/memory_config 的边界。
- `sandbox_root` 当前是"默认 cwd 而非硬沙箱"——绝对路径仍能越界，与 OpenClaw 一致。若需真隔离，未来接入 OS 级沙箱（容器/landlock 等），届时另立设计。
