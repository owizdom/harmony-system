"""Base classes for social media API connectors."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Callable

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    POST = "post"
    LIKE = "like"
    FOLLOW = "follow"
    REPOST = "repost"
    COMMENT = "comment"
    UNFOLLOW = "unfollow"


@dataclass
class StreamEvent:
    """Normalized event emitted by any connector."""

    event_type: EventType
    platform: str
    user_id: str
    username: str
    timestamp: datetime
    raw: dict[str, Any] = field(default_factory=dict)
    target_user_id: str | None = None
    target_username: str | None = None
    content: str | None = None
    media_urls: list[str] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)
    event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "platform": self.platform,
            "user_id": self.user_id,
            "username": self.username,
            "timestamp": self.timestamp.isoformat(),
            "target_user_id": self.target_user_id,
            "target_username": self.target_username,
            "content": self.content,
            "media_urls": self.media_urls,
            "metrics": self.metrics,
            "event_id": self.event_id,
        }


EventCallback = Callable[[StreamEvent], Any]


class BaseConnector(ABC):
    """Abstract base for all platform connectors."""

    PLATFORM: str = ""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._running = False
        self._callbacks: list[EventCallback] = []

    def on_event(self, callback: EventCallback) -> None:
        self._callbacks.append(callback)

    async def _emit(self, event: StreamEvent) -> None:
        for cb in self._callbacks:
            try:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Callback error for %s event", event.event_type)

    @abstractmethod
    async def connect(self) -> None:
        """Authenticate and open the streaming connection."""

    @abstractmethod
    async def stream(self) -> AsyncIterator[StreamEvent]:
        """Yield normalized events from the platform in real time."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close the connection."""

    async def run(self) -> None:
        """Connect and stream events, dispatching to callbacks."""
        await self.connect()
        self._running = True
        logger.info("[%s] Streaming started", self.PLATFORM)
        try:
            async for event in self.stream():
                if not self._running:
                    break
                await self._emit(event)
        finally:
            await self.disconnect()
            self._running = False
            logger.info("[%s] Streaming stopped", self.PLATFORM)

    def stop(self) -> None:
        self._running = False
