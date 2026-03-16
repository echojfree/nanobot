"""Prompt construction helpers for the compact nanobot clone."""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any


def current_time_label() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M")


class ContextBuilder:
    BOOTSTRAP_FILES = ("AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md")

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def build_system_prompt(self) -> str:
        sections = [
            self._identity_block(),
            self._bootstrap_block(),
            self._memory_block(),
            self._skills_block(),
        ]
        return "\n\n---\n\n".join(section for section in sections if section.strip())

    def build_messages(
        self,
        history: list[dict[str, Any]],
        user_text: str,
        session_id: str,
        channel: str = "cli",
    ) -> list[dict[str, Any]]:
        runtime_context = (
            "[Runtime Context]\n"
            f"time: {current_time_label()}\n"
            f"session: {session_id}\n"
            f"channel: {channel}"
        )
        merged_user_text = f"{runtime_context}\n\n{user_text}"
        return [
            {"role": "system", "content": self.build_system_prompt()},
            *history,
            {"role": "user", "content": merged_user_text},
        ]

    def _identity_block(self) -> str:
        runtime = f"{platform.system()} / Python {platform.python_version()}"
        workspace = str(self.workspace)
        return (
            "# Identity\n\n"
            "You are a compact, readable clone of nanobot.\n\n"
            f"Runtime: {runtime}\n"
            f"Workspace: {workspace}\n\n"
            "Rules:\n"
            "- Prefer concise, correct answers.\n"
            "- Use tools only when they materially help.\n"
            "- Keep file operations inside the workspace.\n"
            "- After tool failures, explain the reason before changing approach.\n"
        )

    def _bootstrap_block(self) -> str:
        parts: list[str] = []
        for name in self.BOOTSTRAP_FILES:
            path = self.workspace / name
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"## {name}\n\n{content}")
        return "# Workspace Files\n\n" + "\n\n".join(parts) if parts else ""

    def _memory_block(self) -> str:
        memory_path = self.workspace / "memory" / "MEMORY.md"
        if not memory_path.exists():
            return ""
        content = memory_path.read_text(encoding="utf-8").strip()
        if not content:
            return ""
        return f"# Memory\n\n{content}"

    def _skills_block(self) -> str:
        skills_dir = self.workspace / "skills"
        if not skills_dir.exists():
            return ""

        items: list[str] = []
        for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
            summary = self._skill_summary(skill_file)
            if summary:
                items.append(summary)

        if not items:
            return ""
        joined = "\n".join(f"- {item}" for item in items)
        return f"# Skills\n\n{joined}"

    @staticmethod
    def _skill_summary(path: Path) -> str:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return ""
        headline = lines[0].lstrip("# ").strip()
        detail = ""
        if len(lines) > 1:
            detail = f": {lines[1][:120]}"
        return f"{path.parent.name} -> {headline}{detail}"
