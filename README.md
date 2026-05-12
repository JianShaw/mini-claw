# Mini Claw

Python implementation sketch for a small OpenClaw-style chat app.

## Run The Chat App

Start the local test chat:

```powershell
uv run mini-claw-chat
```

You can also run the module directly:

```powershell
uv run python -m chat.app
```

Then type messages in the terminal:

```text
Mini Claw chat
Type /exit to quit.
you> hello
claw> echo: hello
```

## Core Shape

The root `chat` package is only a local testing app. The `claw` package is where
the real Mini Claw logic should grow.

```text
mini-claw/
  chat/
    app.py       # local CLI chat app for manual testing
  claw/
    agent.py     # placeholder for the Mini Claw runtime
```

## Design

- `chat.app`: reads terminal input and prints the agent response.
- `claw.agent.MiniClaw`: tiny placeholder for the future agent loop.

## Next Step

Grow `claw.agent.MiniClaw.reply` into the real loop:

```text
input text
 -> build context
 -> call model
 -> optionally run tools
 -> return final text
```
