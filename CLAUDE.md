# Mini Claw

A Python implementation of a mini OpenClaw-style agent runtime with a local CLI chat interface.

## Project Structure

```
mini-claw/
  claw/            # core agent runtime
    agent.py       # MiniClaw class — the agent loop
  chat/            # CLI chat app for manual testing
    app.py         # entry point: reads terminal input, prints agent reply
  pyproject.toml   # project config, managed by uv
  uv.lock          # lock file (committed)
```

## Commands

- Run the chat app: `uv run mini-claw-chat` or `uv run python -m chat.app`
- Add a dependency: `uv add <package>`
- Sync environment: `uv sync`

## Architecture

- `claw.agent.MiniClaw` is the core agent runtime. Its `reply(text)` method is the main entry point, designed to evolve into: build context → call model → optionally run tools → return final text.
- `chat.app` is a thin CLI wrapper that creates a `MiniClaw` instance and loops over terminal input. It is for local testing only.

## Conventions

- Python >=3.11
- Use `from __future__ import annotations` in all modules
- Use `uv` for all dependency and environment management
- Keep `chat/` thin — all real logic goes in `claw/`
