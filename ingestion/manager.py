"""IngestionManager — orchestrates multiple connectors and fans out events."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from .base import BaseConnector, EventCallback, StreamEvent

logger = logging.getLogger(__name__)


class IngestionManager:
    """
    Central manager that runs all registered connectors concurrently
    and dispatches normalized StreamEvents to subscribers.

    Usage:
        mgr = IngestionManager()
        mgr.register(TwitterConnector(twitter_cfg))
        mgr.register(MetaGraphConnector(meta_cfg))
        mgr.on_event(my_handler)      # sync or async callback
        await mgr.run()               # blocks until stopped
    """

    def __init__(self, buffer_size: int = 10_000):
        self._connectors: list[BaseConnector] = []
        self._callbacks: list[EventCallback] = []
        self._buffer: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=buffer_size)
        self._running = False
        self._stats: dict[str, int] = {}

    def register(self, connector: BaseConnector) -> None:
        connector.on_event(self._enqueue)
        self._connectors.append(connector)
        self._stats[connector.PLATFORM] = 0

    def on_event(self, callback: EventCallback) -> None:
        self._callbacks.append(callback)

    async def _enqueue(self, event: StreamEvent) -> None:
        try:
            self._buffer.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event buffer full — dropping oldest event")
            self._buffer.get_nowait()
            self._buffer.put_nowait(event)

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._buffer.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            self._stats[event.platform] = self._stats.get(event.platform, 0) + 1

            for cb in self._callbacks:
                try:
                    result = cb(event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception("Dispatch callback error")

    async def run(self) -> None:
        self._running = True
        tasks = [asyncio.create_task(c.run()) for c in self._connectors]
        tasks.append(asyncio.create_task(self._dispatch_loop()))

        logger.info("IngestionManager started with %d connectors", len(self._connectors))
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._running = False

    async def stop(self) -> None:
        self._running = False
        for c in self._connectors:
            c.stop()

    def get_stats(self) -> dict[str, Any]:
        return {
            "connectors": len(self._connectors),
            "platforms": list(self._stats.keys()),
            "events_ingested": dict(self._stats),
            "buffer_size": self._buffer.qsize(),
            "running": self._running,
        }
