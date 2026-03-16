"""Provider abstraction for the compact nanobot clone."""

from __future__ import annotations

import json
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from mini_nanobot.config import AppConfig


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class AssistantReply:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class BaseProvider(ABC):
    def __init__(self, model: str) -> None:
        self.model = model

    @abstractmethod
    async def respond(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantReply:
        raise NotImplementedError


class DemoProvider(BaseProvider):
    """A deterministic provider that emits tool calls from slash commands."""

    def __init__(self) -> None:
        super().__init__("demo")

    async def respond(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantReply:
        del tools
        trailing_tools = self._collect_trailing_tool_messages(messages)
        if trailing_tools:
            return AssistantReply(content=self._summarize_tool_messages(trailing_tools))

        user_text = self._last_user_text(messages)
        if not user_text:
            return AssistantReply(content="No user message found.")

        normalized = self._strip_runtime_prefix(user_text).strip()
        if not normalized:
            return AssistantReply(content="The message was empty after removing runtime metadata.")

        tool_call = self._maybe_make_tool_call(normalized)
        if tool_call:
            return AssistantReply(tool_calls=[tool_call])

        help_text = (
            "Demo provider is active. It preserves the agent/tool loop without needing an API key.\n\n"
            "Try one of these commands:\n"
            "- /list .\n"
            "- /read README.md\n"
            "- /write notes/todo.txt :: hello\n"
            "- /edit notes/todo.txt ::: hello => hello world\n"
            "- /run git status\n"
            "- /remember nanobot uses a tool loop\n"
            "- /search python typer tutorial\n"
            "- /fetch https://example.com\n\n"
            "If you want real model output, switch provider.type to `openai` in ~/.mini_nanobot/config.json."
        )
        return AssistantReply(content=help_text)

    @staticmethod
    def _strip_runtime_prefix(text: str) -> str:
        marker = "\n\n"
        return text.split(marker, 1)[1] if marker in text else text

    @staticmethod
    def _last_user_text(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user":
                return str(message.get("content") or "")
        return ""

    @staticmethod
    def _collect_trailing_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        for message in reversed(messages):
            if message.get("role") != "tool":
                break
            collected.append(message)
        collected.reverse()
        return collected

    @staticmethod
    def _summarize_tool_messages(messages: list[dict[str, Any]]) -> str:
        parts = ["Tool execution summary:"]
        for message in messages:
            name = message.get("name") or "tool"
            content = str(message.get("content") or "").strip()
            parts.append(f"\n{name}\n{content}")
        return "\n".join(parts)

    def _maybe_make_tool_call(self, text: str) -> ToolCall | None:
        patterns: list[tuple[str, str, callable[[re.Match[str]], dict[str, Any]]]] = [
            (r"^/(list|ls)(?:\s+(.*))?$", "list_files", lambda m: {"path": (m.group(2) or ".").strip()}),
            (r"^/(read|cat)\s+(.+)$", "read_file", lambda m: {"path": m.group(2).strip()}),
            (
                r"^/write\s+(\S+)\s+::\s+([\s\S]+)$",
                "write_file",
                lambda m: {"path": m.group(1).strip(), "content": m.group(2)},
            ),
            (
                r"^/edit\s+(\S+)\s+:::\s+([\s\S]+?)\s+=>\s+([\s\S]+)$",
                "edit_file",
                lambda m: {
                    "path": m.group(1).strip(),
                    "old_text": m.group(2),
                    "new_text": m.group(3),
                },
            ),
            (r"^/(run|exec)\s+(.+)$", "run_shell", lambda m: {"command": m.group(2).strip()}),
            (r"^/remember\s+(.+)$", "remember_note", lambda m: {"note": m.group(1).strip()}),
            (r"^/search\s+(.+)$", "web_search", lambda m: {"query": m.group(1).strip()}),
            (r"^/fetch\s+(.+)$", "fetch_url", lambda m: {"url": m.group(1).strip()}),
        ]

        for pattern, name, builder in patterns:
            match = re.match(pattern, text, flags=re.DOTALL)
            if match:
                return ToolCall(id=f"demo-{uuid.uuid4().hex[:8]}", name=name, arguments=builder(match))
        return None


class OpenAICompatibleProvider(BaseProvider):
    def __init__(self, model: str, api_key: str, base_url: str | None = None) -> None:
        super().__init__(model)
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def respond(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantReply:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        completion = await self.client.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        message = choice.message

        content = self._content_to_text(message.content)
        parsed_tool_calls: list[ToolCall] = []
        for tool_call in message.tool_calls or []:
            parsed_tool_calls.append(
                ToolCall(
                    id=tool_call.id,
                    name=tool_call.function.name,
                    arguments=self._load_arguments(tool_call.function.arguments),
                )
            )
        return AssistantReply(content=content, tool_calls=parsed_tool_calls)

    @staticmethod
    def _content_to_text(content: Any) -> str | None:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                text = getattr(item, "text", None)
                if text:
                    parts.append(str(text))
            return "\n".join(parts) if parts else None
        return None

    @staticmethod
    def _load_arguments(arguments: str) -> dict[str, Any]:
        try:
            loaded = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return {"raw_arguments": arguments}
        return loaded if isinstance(loaded, dict) else {"value": loaded}


def build_provider(config: AppConfig) -> BaseProvider:
    provider_type = config.provider.type
    if provider_type == "demo":
        return DemoProvider()

    api_key = config.provider.resolved_api_key()
    if not api_key:
        raise ValueError(
            "provider.type is `openai` but no API key is configured. "
            f"Set provider.apiKey or env {config.provider.api_key_env}."
        )
    return OpenAICompatibleProvider(
        model=config.provider.model or config.agent.model,
        api_key=api_key,
        base_url=config.provider.base_url,
    )
