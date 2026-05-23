# Mini Claw

A mini **OpenClaw-style agent runtime** with CLI and Web UI — built as a hands-on project for learning AI application development.

> [中文版](README_CN.md)

```
Python 3.11+ | FastAPI | React + TypeScript | SQLite | OpenAI-compatible API
```

## What It Does

Mini Claw is an **agent gateway** that orchestrates multi-session conversations, tool calling, expert routing, and scheduled tasks. It's not a chatbot wrapper — it's a runtime that manages the full lifecycle of AI agents.

Key capabilities:

- **Agent Runtime** — Configurable agents with system prompts, tool sets, skills, and model parameters
- **Tool Calling** — 12+ built-in tools (calculator, shell, file ops, web search, git, etc.) following OpenAI function-calling protocol
- **MCP Bridge** — Connect external tool servers via Model Context Protocol
- **Expert & Skill System** — Installable agent templates (Experts) and composable capability packs (Skills)
- **Memory System** — Dual-layer memory (daily + long-term) with hybrid search (keyword + vector embedding)
- **Scheduled Tasks** — Cron-based task scheduler with execution history
- **Multi-Channel** — CLI and Web UI share the same gateway

## Architecture

```
┌─────────────┐     ┌─────────────┐
│  CLI Chat   │     │   Web UI    │
│  (local.py) │     │ (React+TS)  │
└──────┬──────┘     └──────┬──────┘
       │                   │
       └───────┬───────────┘
               │
        ┌──────▼──────┐
        │  Gateway    │   session lookup, agent dispatch, delivery
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
    │ │Built│ │ MCP   │ │Skills │
    │ │-in  │ │Bridge │ │Loader │
    │ │Tools│ │       │ │       │
    │ └─────┘ └───────┘ └───────┘
    │
    │  ┌──────────┐  ┌────────┐
    └──► Memory   │  │Storage │
       │(daily +  │  │(SQLite │
       │ vector)  │  │+ JSONL)│
       └──────────┘  └────────┘
```

## Quick Start

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 18+

```bash
# Install dependencies
uv sync

# Set your API key (DeepSeek or any OpenAI-compatible endpoint)
cp .env.example .env
# edit .env → DEEPSEEK_API_KEY=sk-xxx
```

### CLI Chat

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

Terminal 1 — Backend (port 8000):

```bash
uv run mini-claw-web
```

Terminal 2 — Frontend (port 5173):

```bash
cd web/frontend
npm install   # first time only
npm run dev
```

Open http://localhost:5173

API docs available at http://localhost:8000/docs (Swagger UI)

## Project Structure

```
mini-claw/
├── chat/                    # CLI chat app (thin wrapper)
│   └── app.py
├── claw/                    # Core agent runtime
│   ├── agent.py             # MiniClaw — top-level facade
│   ├── gateway.py           # RuntimeGateway — routing & orchestration
│   ├── agent_runtime/       # Agent lifecycle: config, resolver, store, factory
│   ├── builtin_tools/       # 12+ tools (calculator, shell, web_search, ...)
│   ├── channels/            # Transport adapters (CLI, Web)
│   │   ├── local.py
│   │   └── web/
│   ├── expert/              # Expert template system (marketplace, store, registry)
│   ├── mcp/                 # MCP server bridge (config, connection, tool proxy)
│   ├── memory/              # Dual-layer memory (daily, long-term, vector search)
│   ├── scheduler/           # Cron-based task scheduler with history
│   ├── skills/              # Skill packs (loader, marketplace, registry)
│   ├── storage/             # SQLite schema, migrations, session store
│   ├── ports.py             # Protocol definitions (dependency inversion)
│   ├── tools.py             # ToolsRegistry (OpenAI function-calling format)
│   └── types.py             # Shared data structures
├── web/
│   ├── backend/             # FastAPI app, routers, schemas, services
│   └── frontend/            # React + TypeScript + Tailwind
├── tests/                   # 70+ test files
└── docs/plans/              # Design documents
```

## Technical Highlights

### Agent Runtime

Agents are created from Expert templates and stored as independent runtime configs. Each agent carries its own system prompt, enabled tools, skills, and tunable parameters (`temperature`). The `AgentResolver` resolves an agent ID to a fully-wired runtime ready for execution.

### Tool Calling

`ToolsRegistry` follows the OpenAI function-calling protocol. Built-in tools are split into **safe** (calculator, time, file search) and **dangerous** (shell, python_test) categories. The MCP bridge transparently proxies external tool servers into the same registry.

### Memory System

Two-layer architecture:
- **Daily memory** — auto-updated every N messages, deterministic extraction (no LLM cost)
- **Long-term memory** — distilled from daily candidates via `/memory distill`
- **Hybrid search** — keyword matching + FastEmbed vector similarity over `sqlite-vec`

### Multi-Channel

Both CLI and Web go through the same `RuntimeGateway`. Channel adapters handle transport differences (stdio vs SSE), while the gateway remains channel-agnostic.

### Storage

Unified SQLite database (`data/mini_claw.sqlite`) with schema versioning and WAL mode. Session history also supports append-only JSONL for backward compatibility.

## Testing

```bash
# Run all tests
uv run pytest

# Run specific modules
uv run pytest tests/test_agent_runtime.py tests/test_memory.py -v
```

70+ test files covering all core modules: agent runtime, expert/skill systems, MCP bridge, memory, scheduler, gateway, web APIs, and integration flows.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Runtime | Python 3.11, dataclasses, async/await |
| LLM API | OpenAI-compatible (DeepSeek) with tool calling |
| Backend | FastAPI, Pydantic, SSE-Starlette |
| Frontend | React, TypeScript, Tailwind CSS, Vite |
| Storage | SQLite (WAL mode, sqlite-vec for vector search) |
| Embedding | FastEmbed (local, no API key needed) |
| MCP | Model Context Protocol client |
| Tooling | uv, pytest, pytest-asyncio |

## License

MIT
