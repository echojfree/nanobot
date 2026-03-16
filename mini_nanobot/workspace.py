"""Workspace templates and simple file-based runtime storage."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

DEFAULT_TEMPLATES: dict[str, str] = {
    "AGENTS.md": """# AGENTS

You are a compact learning version of nanobot.

Focus on:
- understanding the user's request
- using tools only when needed
- keeping changes inside the workspace
""",
    "SOUL.md": """# SOUL

Be direct, practical, and explicit about tradeoffs.
""",
    "USER.md": """# USER

Add user preferences here if you want the assistant to remember them.
""",
    "TOOLS.md": """# TOOLS

Core tools:
- list_files
- read_file
- write_file
- edit_file
- run_shell
- remember_note
- web_search
- fetch_url
""",
    "HEARTBEAT.md": """# HEARTBEAT

This compact version keeps the file to mirror the full project, but it does not
run a background scheduler by default.
""",
    "memory/MEMORY.md": """# MEMORY

Persist stable facts that should survive across sessions.
""",
    "memory/HISTORY.md": "",
}


def sync_workspace_templates(workspace: Path) -> list[Path]:
    workspace.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for relative_path, content in DEFAULT_TEMPLATES.items():
        destination = workspace / relative_path
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        created.append(destination)

    (workspace / "skills").mkdir(exist_ok=True)
    (workspace / "sessions").mkdir(exist_ok=True)
    return created


def append_history_entry(workspace: Path, session_id: str, user_text: str, assistant_text: str) -> None:
    history_path = workspace / "memory" / "HISTORY.md"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = (
        f"[{timestamp}] session={session_id}\n"
        f"USER: {user_text.strip()}\n"
        f"ASSISTANT: {assistant_text.strip()}\n\n"
    )
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(block)


def safe_session_filename(session_id: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in session_id)
    return cleaned or "default"
