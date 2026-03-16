"""The compact agent loop plus a tiny gateway runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mini_nanobot.bus import MessageBus, OutboundMessage
from mini_nanobot.context import ContextBuilder
from mini_nanobot.providers import AssistantReply, BaseProvider
from mini_nanobot.tools import ToolRegistry
from mini_nanobot.workspace import append_history_entry, safe_session_filename


class SessionStore:
    def __init__(self, workspace: Path) -> None:
        self.base_dir = workspace / "sessions"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def session_path(self, session_id: str) -> Path:
        return self.base_dir / f"{safe_session_filename(session_id)}.json"

    def load(self, session_id: str) -> list[dict[str, Any]]:
        path = self.session_path(session_id)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return payload if isinstance(payload, list) else []

    def save(self, session_id: str, history: list[dict[str, Any]]) -> None:
        path = self.session_path(session_id)
        path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")

    def count(self) -> int:
        return len(list(self.base_dir.glob("*.json")))


class AgentLoop:
    def __init__(
        self,
        provider: BaseProvider,
        tools: ToolRegistry,
        workspace: Path,
        *,
        max_iterations: int = 6,
        max_history_messages: int = 40,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.workspace = workspace
        self.max_iterations = max_iterations
        self.max_history_messages = max_history_messages
        self.context = ContextBuilder(workspace)
        self.sessions = SessionStore(workspace)

    async def process_direct(self, message: str, session_id: str = "cli:direct") -> str:
        return await self.run_turn(session_id=session_id, user_text=message, channel="cli")

    async def run_turn(self, session_id: str, user_text: str, channel: str) -> str:
        history = self.sessions.load(session_id)[-self.max_history_messages :]
        turn_history: list[dict[str, Any]] = [{"role": "user", "content": user_text}]
        messages = self.context.build_messages(history, user_text, session_id=session_id, channel=channel)

        final_text = ""
        for _ in range(self.max_iterations):
            reply = await self.provider.respond(messages, self.tools.definitions())
            assistant_entry = self._assistant_entry(reply)
            messages.append(assistant_entry)
            turn_history.append(assistant_entry)

            if not reply.has_tool_calls:
                final_text = reply.content or ""
                break

            for tool_call in reply.tool_calls:
                result = await self.tools.execute(tool_call.name, tool_call.arguments)
                tool_entry = {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.name,
                    "content": result,
                }
                messages.append(tool_entry)
                turn_history.append(tool_entry)
        else:
            final_text = "Stopped after hitting the maximum tool iteration count."
            turn_history.append({"role": "assistant", "content": final_text})

        persisted = (history + turn_history)[-self.max_history_messages :]
        self.sessions.save(session_id, persisted)
        append_history_entry(self.workspace, session_id, user_text, final_text)
        return final_text

    @staticmethod
    def _assistant_entry(reply: AssistantReply) -> dict[str, Any]:
        entry: dict[str, Any] = {"role": "assistant", "content": reply.content}
        if reply.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in reply.tool_calls
            ]
        return entry


class GatewayRuntime:
    """A minimal gateway that demonstrates message-bus based processing."""

    def __init__(self, bus: MessageBus, agent: AgentLoop) -> None:
        self.bus = bus
        self.agent = agent
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            inbound = await self.bus.consume_inbound()
            session_id = f"{inbound.channel}:{inbound.chat_id}"
            response = await self.agent.run_turn(
                session_id=session_id,
                user_text=inbound.content,
                channel=inbound.channel,
            )
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    content=response,
                    metadata={"session_id": session_id},
                )
            )

    def stop(self) -> None:
        self._running = False
