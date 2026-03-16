"""A tiny in-process message bus."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class InboundMessage:
    channel: str
    chat_id: str
    sender_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OutboundMessage:
    channel: str
    chat_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class MessageBus:
    def __init__(self) -> None:
        self._inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, message: InboundMessage) -> None:
        await self._inbound.put(message)

    async def publish_outbound(self, message: OutboundMessage) -> None:
        await self._outbound.put(message)

    async def consume_inbound(self) -> InboundMessage:
        return await self._inbound.get()

    async def consume_outbound(self) -> OutboundMessage:
        return await self._outbound.get()
