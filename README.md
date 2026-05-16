# Mini Claw

Python implementation sketch for a small OpenClaw-style chat app with multi-session support.

## Run The Chat App

Start the local test chat:

```powershell
uv run mini-claw-chat
```

You can also run the module directly:

```powershell
uv run python -m chat.app
```

### Debug & Logging

All log levels are written to `logs/mini-claw.log` automatically. Use flags to control console output:

```powershell
# Normal: console quiet, logs go to file only
uv run mini-claw-chat

# Show all DEBUG logs on console too
uv run mini-claw-chat --debug

# Only show scheduler logs on console
uv run mini-claw-chat --debug-only claw.scheduler
```

Then type messages in the terminal:

```text
Mini Claw chat
Type /help for commands, /exit to quit.
you> hello
claw> echo: hello
```

## Session Commands

The chat app supports managing multiple independent conversations:

| Command | Description |
|---------|-------------|
| `/new` | Create a new session and switch to it |
| `/sessions` | List all sessions (active one marked with `*`) |
| `/select <id>` | Switch to a specific session |
| `/delete <id>` | Delete a session |
| `/compact` | Compress current session context into a summary |
| `/mcp` | Show MCP server connection status |
| `/memory today` | View today's daily memory |
| `/memory long` | View long-term memory |
| `/memory update` | Force update today's daily memory from current session |
| `/memory distill` | Extract long-term candidates from daily memory into MEMORY.md |
| `/tasks` | List all scheduled tasks and their last execution result |
| `/task run <name>` | Manually trigger a scheduled task |
| `/help` | Show available commands |

### Memory System

Memory is managed at two levels:

- **Daily memory** (`data/memory/daily/YYYY-MM-DD.md`) — short-term context, auto-updated every 3 user messages, or on-demand via `/memory update`.
- **Long-term memory** (`data/memory/MEMORY.md`) — stable preferences and decisions, populated from daily candidates via `/memory distill`.

Both are automatically injected into every LLM call as background context. User messages containing keywords like "prefer", "remember", "希望", "记住" are flagged as long-term candidates.

Example workflow:

```text
you> tell me about sorting algorithms
claw> ...
you> /new
New session: sess_a1b2c3d4
you> /sessions
  sess_e5f6g7h8 *
  sess_a1b2c3d4
you> /select sess_e5f6g7h8
Switched to sess_e5f6g7h8
you> /compact
Compacted. Summary:
  用户询问了排序算法...
you> continue from where we left off
claw> ...
```

## Project Structure

```text
mini-claw/
  chat/
    app.py           # local CLI chat app with session commands
  claw/
    agent.py         # MiniClaw facade — composes all runtime modules
    gateway.py       # RuntimeGateway — session/agent/delivery orchestration
    session.py       # session management (InMemory, Jsonl stores)
    deepseek.py      # DeepSeek agent runner with thinking mode
    ports.py         # Protocol definitions (dependency inversion)
    types.py         # shared data structures (Session, ChatMessage, etc.)
    channels/
      local.py       # CLI transport, adapter, delivery implementations
    processor.py     # message pipeline (dedup, validate, filter)
    runner.py        # EchoAgentRunner for testing
  docs/plans/        # design documents
  tests/             # test suite
```

## Design

### Multi-Session Architecture

Sessions use a two-level hierarchy:

- **peer_key** (`channel:account_id:peer_id`) — groups all sessions for a user
- **session_id** (`sess_xxx`) — identifies an individual conversation

Each user has one active session. Messages are routed to the active session automatically.

### Storage

Sessions persist to disk as JSONL files:

```text
data/sessions/
  index.json            # peer → {active, sessions: {id: metadata}}
  sess_a1b2c3d4.jsonl   # one ChatMessage per line (append-only)
```

### Context Compression

`/compact` calls the LLM to generate a summary of the conversation. The summary is stored in session metadata, and in-memory history is cleared. The LLM context becomes: `system(summary) + recent messages`. Original JSONL records are preserved on disk.
