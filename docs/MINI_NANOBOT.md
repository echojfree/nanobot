# Mini Nanobot

`mini_nanobot` is a compact, runnable rewrite of the core nanobot architecture.
It is meant for reading, debugging, and experimentation.

## Scope

Included:

- config loading and saving
- workspace bootstrap files
- system prompt assembly
- session history persistence
- tool registry and tool execution
- provider abstraction
- iterative agent loop
- direct CLI mode
- minimal gateway mode using an in-process message bus

Deliberately omitted:

- chat platform integrations
- cron and heartbeat scheduling
- MCP servers
- multimodal media handling
- provider-specific edge cases

## File Map

Full project to compact rewrite:

- `nanobot/cli/commands.py` -> `mini_nanobot/cli.py`
- `nanobot/config/*` -> `mini_nanobot/config.py`
- `nanobot/agent/context.py` -> `mini_nanobot/context.py`
- `nanobot/agent/tools/*` -> `mini_nanobot/tools.py`
- `nanobot/providers/*` -> `mini_nanobot/providers.py`
- `nanobot/agent/loop.py` -> `mini_nanobot/agent.py`
- `nanobot/bus/*` -> `mini_nanobot/bus.py`
- `nanobot/templates/*` -> `mini_nanobot/workspace.py`

## How To Run

```powershell
uv run mini-nanobot onboard
uv run mini-nanobot status
uv run mini-nanobot agent
uv run mini-nanobot gateway
uv run mini-nanobot gateway -m "/list ."
```

## Provider Modes

### Demo provider

Default mode. No API key required.

It accepts slash commands and turns them into tool calls so you can see the
tool loop clearly:

- `/list .`
- `/read README.md`
- `/write notes/todo.txt :: hello`
- `/edit notes/todo.txt ::: hello => hello world`
- `/run git status`
- `/remember nanobot uses a provider abstraction`
- `/search python typer`
- `/fetch https://example.com`

### OpenAI-compatible provider

Edit `~/.mini_nanobot/config.json`:

```json
{
  "provider": {
    "type": "openai",
    "model": "gpt-4o-mini",
    "apiKeyEnv": "OPENAI_API_KEY"
  }
}
```

Then set the environment variable and run `mini-nanobot agent`.

## Reading Order

1. `mini_nanobot/cli.py`
2. `mini_nanobot/agent.py`
3. `mini_nanobot/context.py`
4. `mini_nanobot/tools.py`
5. `mini_nanobot/providers.py`
6. `mini_nanobot/config.py`
7. `mini_nanobot/workspace.py`

## Why This Exists

The full nanobot project is optimized for breadth: more providers, more
channels, more deployment modes.

This version is optimized for legibility:

- fewer files
- fewer abstractions
- smaller data model
- one obvious runtime path
