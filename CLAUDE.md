# Mini Claw

A Python implementation of a mini OpenClaw-style agent runtime with a local CLI chat interface.

## Project Structure

```
mini-claw/
  claw/            # core agent runtime
    agent.py       # MiniClaw class — the agent loop
    session.py     # session management (InMemorySessionStore, JsonlSessionStore)
    gateway.py     # RuntimeGateway — session/agent/delivery orchestration
    deepseek.py    # DeepSeek agent runner with thinking mode
    ports.py       # Protocol definitions (dependency inversion)
    types.py       # shared data structures (Session, ChatMessage, etc.)
  chat/            # CLI chat app for manual testing
    app.py         # entry point: reads terminal input, prints agent reply
  docs/plans/      # design documents
  tests/           # test suite
  pyproject.toml   # project config, managed by uv
  uv.lock          # lock file (committed)
```

## Commands

- Run the chat app: `uv run mini-claw-chat` or `uv run python -m chat.app`
- Add a dependency: `uv add <package>`
- Sync environment: `uv sync`

## CLI Commands (in chat app)

- `/new` — create a new session and activate it
- `/sessions` — list all sessions for current user
- `/select <session_id>` — switch to a specific session
- `/delete <session_id>` — delete a session
- `/compact` — compress current session context (LLM generates summary)
- `/help` — show available commands
- `/exit` or `/quit` — exit the app

## Architecture

### Multi-Session Design

Sessions use a two-level hierarchy:
- **peer_key** (`channel:account_id:peer_id`) — groups all sessions for a user
- **session_id** (`sess_xxx`) — identifies an individual conversation

Each peer has one "active" session. Messages are routed to the active session automatically.

### Storage

- `data/sessions/index.json` — maps peer_key → {active, sessions: {id: meta}}
- `data/sessions/{session_id}.jsonl` — one ChatMessage per line (append-only)
- Compact writes summary to index, original JSONL records are preserved

### Context Compression (`/compact`)

When triggered, the LLM generates a summary of the conversation history. The summary is stored in `session.summary`, history is cleared in memory. The LLM context becomes: `system(summary) + recent messages`. Original JSONL records remain on disk.

### Module Responsibilities

- `claw.agent.MiniClaw` — facade, composes all dependencies, exposes session management methods
- `claw.gateway.RuntimeGateway` — orchestrates session lookup/creation, agent execution, delivery
- `claw.session` — `InMemorySessionStore` (testing), `JsonlSessionStore` (production), session creation helpers
- `claw.ports` — Protocol definitions for SessionStore, AgentRunner, Delivery, etc.
- `chat.app` — thin CLI wrapper, command parsing, no business logic

## Conventions

- Python >=3.11
- Use `from __future__ import annotations` in all modules
- Use `uv` for all dependency and environment management
- Keep `chat/` thin — all real logic goes in `claw/`

## 开发中需要注意

- 默认使用PLAN模式进行规划之后。
- PLAN规划的方案，确认其中有相应的test方案，注意边界问题。
- 将确认好的PLAN保存到docs目录下，如果PLAN有更新迭代，一定要同步更新docs下的文档。
- 业务代码要有清晰的注释
