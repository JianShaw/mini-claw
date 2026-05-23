# Mini Claw

一个迷你版 **OpenClaw 风格的 Agent 运行时**，带 CLI 和 Web UI —— 作为 AI 应用开发的练手项目。

> [English](README.md)

```
Python 3.11+ | FastAPI | React + TypeScript | SQLite | OpenAI 兼容 API
```

## 做了什么

Mini Claw 是一个 **Agent 网关**，编排多会话对话、工具调用、专家路由和定时任务。它不是套壳聊天机器人，而是管理 AI Agent 完整生命周期的运行时。

核心能力：

- **Agent 运行时** — 可配置的 Agent，支持自定义系统提示词、工具集、技能包和模型参数
- **工具调用** — 12+ 内置工具（计算器、Shell、文件操作、网页搜索、Git 等），遵循 OpenAI function-calling 协议
- **MCP 桥接** — 通过 Model Context Protocol 连接外部工具服务器
- **专家与技能系统** — 可安装的 Agent 模板（Expert）和可组合的能力包（Skill）
- **记忆系统** — 双层记忆（日常 + 长期），支持混合搜索（关键词 + 向量嵌入）
- **定时任务** — 基于 Cron 的任务调度器，带执行历史
- **多通道** — CLI 和 Web UI 共享同一网关

## 架构

```
┌─────────────┐     ┌─────────────┐
│  CLI Chat   │     │   Web UI    │
│  (local.py) │     │ (React+TS)  │
└──────┬──────┘     └──────┬──────┘
       │                   │
       └───────┬───────────┘
               │
        ┌──────▼──────┐
        │  Gateway    │   会话查找、Agent 分发、消息投递
        └──────┬──────┘
               │
    ┌──────────┼──────────────┐
    │          │              │
┌───▼───┐ ┌───▼────┐ ┌──────▼──────┐
│ Agent │ │ Tool   │ │  Scheduler  │
│Runtime│ │Registry│ │  (cron)     │
└───┬───┘ └───┬────┘ └─────────────┘
    │         │
    │    ┌────┴────┬──────────┐
    │    │         │          │
    │ ┌──▼──┐ ┌───▼───┐ ┌───▼───┐
    │ │内置  │ │ MCP   │ │Skills │
    │ │工具  │ │桥接   │ │加载器 │
    │ └─────┘ └───────┘ └───────┘
    │
    │  ┌──────────┐  ┌────────┐
    └──► 记忆系统  │  │存储层  │
       │(日常 +   │  │(SQLite │
       │ 向量)    │  │+ JSONL)│
       └──────────┘  └────────┘
```

## 快速启动

**前置条件：** Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 18+

```bash
# 安装依赖
uv sync

# 设置 API Key（DeepSeek 或任何 OpenAI 兼容端点）
cp .env.example .env
# 编辑 .env → DEEPSEEK_API_KEY=sk-xxx
```

### CLI 聊天

```bash
uv run mini-claw-chat
```

```
Mini Claw chat
Type /help for commands, /exit to quit.
you> hello
claw> echo: hello
```

### Web UI

终端 1 — 后端（端口 8000）：

```bash
uv run mini-claw-web
```

终端 2 — 前端（端口 5173）：

```bash
cd web/frontend
npm install   # 首次安装
npm run dev
```

打开 http://localhost:5173

API 文档：http://localhost:8000/docs（Swagger UI）

## 项目结构

```
mini-claw/
├── chat/                    # CLI 聊天应用（薄封装）
│   └── app.py
├── claw/                    # 核心 Agent 运行时
│   ├── agent.py             # MiniClaw — 顶层门面类
│   ├── gateway.py           # RuntimeGateway — 路由与编排
│   ├── agent_runtime/       # Agent 生命周期：配置、解析、存储、工厂
│   ├── builtin_tools/       # 12+ 内置工具（计算器、Shell、网页搜索…）
│   ├── channels/            # 传输适配器（CLI、Web）
│   │   ├── local.py
│   │   └── web/
│   ├── expert/              # 专家模板系统（市场、存储、注册）
│   ├── mcp/                 # MCP 服务器桥接（配置、连接、工具代理）
│   ├── memory/              # 双层记忆（日常、长期、向量搜索）
│   ├── scheduler/           # Cron 定时任务调度器
│   ├── skills/              # 技能包（加载器、市场、注册）
│   ├── storage/             # SQLite schema、迁移、会话存储
│   ├── ports.py             # Protocol 定义（依赖倒置）
│   ├── tools.py             # ToolsRegistry（OpenAI function-calling 格式）
│   └── types.py             # 共享数据结构
├── web/
│   ├── backend/             # FastAPI 应用、路由、Schema、服务
│   └── frontend/            # React + TypeScript + Tailwind
├── tests/                   # 70+ 测试文件
└── docs/plans/              # 设计文档
```

## 技术亮点

### Agent 运行时

Agent 从 Expert 模板创建，存储为独立的运行时配置。每个 Agent 携带自己的系统提示词、启用的工具、技能和可调参数（`temperature`）。`AgentResolver` 将 Agent ID 解析为完整的可执行运行时。

### 工具调用

`ToolsRegistry` 遵循 OpenAI function-calling 协议。内置工具分为**安全**（calculator、time、file_search）和**危险**（shell、python_test）两类。MCP 桥接将外部工具服务器透明地代理进同一注册表。

### 记忆系统

双层架构：
- **日常记忆** — 每 N 条消息自动更新，确定性提取（无 LLM 开销）
- **长期记忆** — 通过 `/memory distill` 从日常记忆中提炼
- **混合搜索** — 关键词匹配 + FastEmbed 向量相似度（基于 `sqlite-vec`）

### 多通道

CLI 和 Web 都通过同一个 `RuntimeGateway`。通道适配器处理传输差异（stdio vs SSE），网关本身不感知通道。

### 存储层

统一的 SQLite 数据库（`data/mini_claw.sqlite`），支持 schema 版本管理和 WAL 模式。会话历史同时支持 JSONL 追加写入以兼容旧版。

## 测试

```bash
# 运行全部测试
uv run pytest

# 运行特定模块
uv run pytest tests/test_agent_runtime.py tests/test_memory.py -v
```

70+ 测试文件覆盖所有核心模块：Agent 运行时、专家/技能系统、MCP 桥接、记忆系统、调度器、网关、Web API 和集成流程。

## 技术栈

| 层级 | 技术 |
|------|------|
| Agent 运行时 | Python 3.11, dataclasses, async/await |
| LLM API | OpenAI 兼容（DeepSeek），支持 tool calling |
| 后端 | FastAPI, Pydantic, SSE-Starlette |
| 前端 | React, TypeScript, Tailwind CSS, Vite |
| 存储 | SQLite（WAL 模式，sqlite-vec 向量搜索） |
| 嵌入 | FastEmbed（本地运行，无需 API Key） |
| MCP | Model Context Protocol 客户端 |
| 工具链 | uv, pytest, pytest-asyncio |

## License

MIT
