# PLAN: pi Git Tools Extension

> 目标：为 pi 编写一个 extension，把常用 git 操作封装成结构化工具（`git_*`），供 LLM 在对话中直接、可靠地调用。危险操作通过 `ctx.ui.confirm()` 弹窗确认。

## 1. 背景

pi 内置只有 `bash` 能跑 git，没有专用的 git tool / skill。每次让 agent 操作 git 都要走 bash + 自然语言拼命令，存在：
- 输出未经裁剪（`git diff` 可能很大）
- 没有统一的危险操作拦截
- LLM 容易拼错参数 / 误用 `--force`

本 extension 通过 `pi.registerTool()` 注册结构化工具，统一入口与安全策略。

## 2. 范围

| 类别 | 工具 | 是否有副作用 | 是否需要确认 |
|------|------|--------------|----------------|
| 只读 | `git_status` | ❌ | 否 |
| 只读 | `git_diff` | ❌ | 否 |
| 只读 | `git_log` | ❌ | 否 |
| 只读 | `git_branch_list` | ❌ | 否 |
| 只读 | `git_show` | ❌ | 否 |
| 提交 | `git_add` | ✅ 改 index | 否 |
| 提交 | `git_commit` | ✅ 新增 commit | 否 |
| 远程 | `git_fetch` | ✅ 改 refs | 否 |
| 远程 | `git_pull` | ✅ 改工作区 | **是** |
| 远程 | `git_push` | ✅ 改远程 | **是** |
| 分支 | `git_checkout` | ✅ 改工作区 | 否（普通）/ **是**（`--`丢弃路径） |
| 分支 | `git_branch_create` | ✅ 新分支 | 否 |
| 分支 | `git_merge` | ✅ 改工作区 | **是** |
| 分支 | `git_rebase` | ✅ 改工作区 | **是** |
| 重置 | `git_reset` | ✅ 改历史/工作区 | **是** |

> 设计原则：**读操作永远放行；本地写操作（add/commit/checkout 分支/fetch）放行；影响工作区或远程的破坏性操作（pull/push/merge/rebase/reset/checkout -- path）弹窗确认。**
> 若 `ctx.hasUI === false`（print/json 模式），危险操作直接拒绝并返回提示，绝不静默执行。

## 3. 目录结构

放在**用户全局**位置，所有项目可用：

```
~/.pi/agent/extensions/git-tools/
├── index.ts          # 入口：注册所有 git_* 工具
├── git.ts            # 核心：命令执行 + 输出解析 + 确认策略
├── package.json      # 声明依赖入口（无外部 npm 依赖）
└── README.md         # 使用说明
```

Windows 上对应 `C:/Users/shaojian/.pi/agent/extensions/git-tools/`。

> 选择全局而非项目内 `.pi/extensions`：git 操作是跨项目通用能力，且不污染 mini-claw 仓库。

## 4. 核心设计

### 4.1 命令执行层（`git.ts`）

```typescript
// 统一封装 pi.exec("git", [...args], { cwd, signal })
interface GitResult {
  ok: boolean;        // exit code === 0
  stdout: string;
  stderr: string;
  code: number;
}

async function runGit(pi, args, ctx, opts?: { trim?: boolean }): Promise<GitResult>
```

约定：
- 所有命令显式带 `ctx.cwd`（不依赖 shell 的 cwd）
- 始终透传 `signal`，支持 Esc 取消
- 失败不抛异常，把 `ok/code/stderr` 交给工具返回，让 LLM 看到真实错误（例如 "not a git repo"、"nothing to commit"）

### 4.2 仓库检测

每个工具执行前先 `git rev-parse --git-dir`：
- 失败 → 返回 `{ ok:false, error:"not a git repository" }`，不弹窗、不执行
- 成功 → 继续

避免在非 git 目录误操作。

### 4.3 危险操作确认策略

```typescript
const DANGEROUS = new Set(["git_pull","git_push","git_merge","git_rebase","git_reset"]);
// git_checkout 当用于丢弃路径（带 `--` 或 `--force`）时也算危险

async function guardDangerous(ctx, toolName, summary): Promise<boolean> {
  if (!ctx.hasUI) {
    // 非交互模式：拒绝
    return false;
  }
  return ctx.ui.confirm(`确认执行 ${toolName}?`, summary);
}
```

被拒绝时返回结构化错误，告诉 LLM "用户取消"。

### 4.4 输出裁剪

| 工具 | 裁剪策略 |
|------|----------|
| `git_diff` | 默认 `--stat` 概览 + 完整 diff，超过 8000 字符时截断并附提示 |
| `git_log` | 默认 `--max-count=20`，结构化为 `{hash, author, date, message}` 数组 |
| `git_status` | `--porcelain`，解析为 `{staged, unstaged, untracked, ahead, behind}` |
| `git_show` | 限制单次输出长度 |

### 4.5 工具 schema 示例

```typescript
pi.registerTool({
  name: "git_commit",
  label: "Git Commit",
  description: "Stage and create a git commit with a message.",
  promptSnippet: "Create a git commit with a conventional message",
  promptGuidelines: [
    "Use git_commit (not raw bash) when the user asks to commit, so the message and staging are validated."
  ],
  parameters: Type.Object({
    message: Type.String({ description: "Commit message" }),
    paths: Type.Optional(Type.Array(Type.String()), { description: "Paths to stage; omit to commit all tracked changes" }),
    amend: Type.Optional(Type.Boolean()),
  }),
  async execute(_id, params, signal, _onUpdate, ctx) { ... }
});
```

## 5. 测试方案

extension 是 TS 跑在 pi 运行时内，无法像 Python 那样 `pytest`。采用**分层验证**：

### 5.1 纯函数单测（`git.test.ts`，用 node:test）
对**纯解析函数**做单测，不依赖 pi：
- `parseStatus(porcelainOutput)` → 结构化对象
- `parseLog(rawLog)` → 提交数组
- `summarizeDiff(diffText, maxLen)` → 截断逻辑
- `isDangerous(toolName, args)` → 危险判定

边界用例：
- 空 porcelain / 空 diff
- 超长 diff（触发截断）
- 含中文/特殊字符的提交信息
- 二进制文件 diff（`Binary files ... differ`）
- merge 冲突状态 `UU`
- ahead/behind 同时存在

### 5.2 集成验证脚本（`scripts/smoke.sh`）
在临时 git 仓库里端到端跑一遍（手动或 CI）：
```bash
tmp=$(mktemp -d); cd $tmp; git init
echo a > f.txt; git add f.txt; git commit -m "init"
# 用 pi -p（print 模式）触发各工具，检查输出
```
验证：status/diff/log/commit/branch_create 正常；push/pull/merge 在 print 模式下被正确拒绝。

### 5.3 手动 TUI 验证清单
- [ ] 读类工具在对话中可被 LLM 调用且输出清晰
- [ ] `git_commit` 能正确 staging + 提交
- [ ] `git_push` / `git_merge` 弹出确认框，拒绝时不执行
- [ ] print/json 模式下危险操作被拒绝（不弹窗、不执行）
- [ ] 非 git 目录下工具返回友好错误
- [ ] Esc 能中断长时间命令

## 6. 边界与风险

| 边界 | 处理 |
|------|------|
| 非 git 目录 | `rev-parse` 检测，返回明确错误 |
| 危险操作在非交互模式 | 直接拒绝，不静默执行 |
| 超大 diff/log | 截断 + 提示，避免撑爆上下文 |
| `git_commit` 空提交 | 检测 `nothing to commit`，返回提示而非报错 |
| 推送被拒（无权限/非快进） | 透传 stderr，LLM 据此反馈 |
| 中文/特殊字符文件名 | `git` 默认带引号转义，用 `-z` 或解析时处理 |
| 与 built-in bash 工具冲突 | 工具名带 `git_` 前缀，不覆盖任何内置工具 |
| signal 取消 | 所有 `pi.exec` 透传 signal |

## 7. 实现步骤

1. 创建 `~/.pi/agent/extensions/git-tools/` 目录及 `package.json`
2. 实现 `git.ts`：`runGit` / `ensureRepo` / 解析函数 / `guardDangerous` / 常量
3. 实现 `index.ts`：注册全部 15 个工具
4. 写 `git.test.ts` 纯函数单测并跑通
5. 写 `scripts/smoke.sh` 集成验证
6. `pi` 启动后在真实仓库手动验证清单
7. 按 CLAUDE.md 规则走 `codex:adversarial-review` 闭环（如可用）
8. 同步本文档（如有调整）

## 8. 不做（Out of Scope）

- 不实现 `git rebase -i` 交互式编辑（太复杂，留给 bash）
- 不做 GitHub/GitLab PR 集成（那是 MCP server 的职责）
- 不实现 git 钩子注入（与本项目无关）
- 不替换 built-in bash（用户仍可直接用 bash 跑任意 git 命令）
