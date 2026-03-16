"""Small but complete tool system for the compact nanobot clone."""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from ddgs import DDGS
from readability import Document


def _trim_text(text: str, limit: int = 8_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[truncated]"


class Tool(ABC):
    name: str
    description: str
    schema: dict[str, Any]

    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def definitions(self) -> list[dict[str, Any]]:
        return [tool.definition() for tool in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Unknown tool: {name}"

        try:
            result = await tool.execute(**arguments)
        except Exception as exc:
            return f"Tool `{name}` failed: {exc}"
        return _trim_text(result)


class _WorkspaceTool(Tool):
    def __init__(self, workspace: Path, restrict_to_workspace: bool, max_file_bytes: int) -> None:
        self.workspace = workspace
        self.restrict_to_workspace = restrict_to_workspace
        self.max_file_bytes = max_file_bytes

    def resolve_path(self, raw_path: str, *, must_exist: bool = False) -> Path:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = (self.workspace / candidate).resolve()
        else:
            candidate = candidate.resolve()

        if self.restrict_to_workspace:
            workspace_root = self.workspace.resolve()
            try:
                candidate.relative_to(workspace_root)
            except ValueError as exc:
                raise ValueError(f"path escapes workspace: {candidate}") from exc

        if must_exist and not candidate.exists():
            raise FileNotFoundError(candidate)
        return candidate


class ListFilesTool(_WorkspaceTool):
    name = "list_files"
    description = "List files and directories relative to the workspace."
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "default": "."},
        },
        "required": [],
        "additionalProperties": False,
    }

    async def execute(self, path: str = ".") -> str:
        target = self.resolve_path(path, must_exist=True)
        if not target.is_dir():
            raise ValueError(f"not a directory: {target}")

        rows: list[str] = [f"Listing for {target}"]
        for child in sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            label = "[dir]" if child.is_dir() else f"[file {child.stat().st_size}b]"
            rows.append(f"{label} {child.name}")
        return "\n".join(rows)


class ReadFileTool(_WorkspaceTool):
    name = "read_file"
    description = "Read a UTF-8 text file from the workspace."
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "default": 1},
            "end_line": {"type": "integer", "default": 200},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def execute(self, path: str, start_line: int = 1, end_line: int = 200) -> str:
        target = self.resolve_path(path, must_exist=True)
        if target.is_dir():
            raise ValueError(f"cannot read a directory: {target}")
        if target.stat().st_size > self.max_file_bytes:
            raise ValueError(f"file too large: {target.stat().st_size} bytes")

        lines = target.read_text(encoding="utf-8").splitlines()
        start = max(1, start_line)
        end = max(start, end_line)
        excerpt = lines[start - 1 : end]
        numbered = [f"{index}: {line}" for index, line in enumerate(excerpt, start)]
        header = f"File: {target} ({len(lines)} lines)"
        return header + "\n" + "\n".join(numbered)


class WriteFileTool(_WorkspaceTool):
    name = "write_file"
    description = "Write or overwrite a text file inside the workspace."
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    async def execute(self, path: str, content: str) -> str:
        target = self.resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {target}"


class EditFileTool(_WorkspaceTool):
    name = "edit_file"
    description = "Replace text inside a workspace file."
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "old_text", "new_text"],
        "additionalProperties": False,
    }

    async def execute(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
    ) -> str:
        target = self.resolve_path(path, must_exist=True)
        text = target.read_text(encoding="utf-8")

        count = text.count(old_text)
        if count == 0:
            raise ValueError("old_text not found")

        if count > 1 and not replace_all:
            raise ValueError("old_text matched multiple locations; set replace_all=true")

        updated = text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
        target.write_text(updated, encoding="utf-8")
        return f"Updated {target}; replacements={count if replace_all else 1}"


class ShellTool(Tool):
    name = "run_shell"
    description = "Run a shell command in the workspace."
    schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 20},
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def __init__(self, workspace: Path, default_timeout: int) -> None:
        self.workspace = workspace
        self.default_timeout = default_timeout

    async def execute(self, command: str, timeout: int | None = None) -> str:
        effective_timeout = timeout or self.default_timeout
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return f"Command timed out after {effective_timeout}s"

        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        parts = [f"Exit code: {process.returncode}"]
        if out:
            parts.append("STDOUT:\n" + out)
        if err:
            parts.append("STDERR:\n" + err)
        return "\n\n".join(parts)


class RememberTool(Tool):
    name = "remember_note"
    description = "Append a durable note to memory/MEMORY.md."
    schema = {
        "type": "object",
        "properties": {
            "note": {"type": "string"},
        },
        "required": ["note"],
        "additionalProperties": False,
    }

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    async def execute(self, note: str) -> str:
        memory_path = self.workspace / "memory" / "MEMORY.md"
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        with memory_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n- {note.strip()}\n")
        return f"Stored note in {memory_path}"


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the public web with DuckDuckGo."
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 5},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, max_results: int) -> None:
        self.max_results = max_results

    async def execute(self, query: str, max_results: int = 5) -> str:
        count = max_results or self.max_results

        def _search() -> list[dict[str, Any]]:
            with DDGS() as client:
                return list(client.text(query, max_results=count))

        results = await asyncio.to_thread(_search)
        if not results:
            return f"No search results for: {query}"

        lines = [f"Results for: {query}"]
        for index, item in enumerate(results, 1):
            title = item.get("title") or "(no title)"
            href = item.get("href") or ""
            body = item.get("body") or ""
            lines.append(f"{index}. {title}\n   {href}\n   {body}")
        return "\n".join(lines)


class FetchUrlTool(Tool):
    name = "fetch_url"
    description = "Download a URL and extract readable text."
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "max_chars": {"type": "integer", "default": 5000},
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    async def execute(self, url: str, max_chars: int = 5000) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"unsupported URL scheme: {parsed.scheme}")

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            body = response.text

        if "html" in response.headers.get("content-type", ""):
            doc = Document(body)
            text = doc.summary(html_partial=True)
            clean = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<p>", "\n").replace("</p>", "\n")
            clean = re.sub(r"<[^>]+>", " ", clean)
        else:
            clean = body

        return f"URL: {url}\n\n{_trim_text(clean.strip(), max_chars)}"


def build_default_tool_registry(
    workspace: Path,
    *,
    allow_shell: bool,
    allow_web: bool,
    shell_timeout: int,
    max_file_bytes: int,
    restrict_to_workspace: bool,
    web_result_count: int,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ListFilesTool(workspace, restrict_to_workspace, max_file_bytes))
    registry.register(ReadFileTool(workspace, restrict_to_workspace, max_file_bytes))
    registry.register(WriteFileTool(workspace, restrict_to_workspace, max_file_bytes))
    registry.register(EditFileTool(workspace, restrict_to_workspace, max_file_bytes))
    registry.register(RememberTool(workspace))

    if allow_shell:
        registry.register(ShellTool(workspace, shell_timeout))
    if allow_web:
        registry.register(WebSearchTool(web_result_count))
        registry.register(FetchUrlTool())
    return registry
