"""Meta Graph API connector — Instagram & Facebook pages/posts via webhooks + polling."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import aiohttp
from aiohttp import web

from .base import BaseConnector, EventType, StreamEvent

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com/v19.0"


class MetaGraphConnector(BaseConnector):
    """Real-time ingestion from Meta Graph API (Facebook + Instagram)."""

    PLATFORM = "meta"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._access_token: str = config["access_token"]
        self._app_secret: str = config.get("app_secret", "")
        self._verify_token: str = config.get("verify_token", "meta_verify")
        self._page_ids: list[str] = config.get("page_ids", [])
        self._ig_user_ids: list[str] = config.get("ig_user_ids", [])
        self._webhook_port: int = config.get("webhook_port", 8443)
        self._poll_interval: int = config.get("poll_interval_sec", 30)
        self._session: aiohttp.ClientSession | None = None
        self._webhook_runner: web.AppRunner | None = None
        self._event_queue: asyncio.Queue[StreamEvent] = asyncio.Queue()

    def _params(self, extra: dict | None = None) -> dict[str, str]:
        p = {"access_token": self._access_token}
        if extra:
            p.update(extra)
        return p

    # --- lifecycle ---

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        await self._start_webhook_server()
        await self._subscribe_webhooks()
        logger.info("[meta] Connected — webhook on :%d, tracking %d pages, %d IG accounts",
                     self._webhook_port, len(self._page_ids), len(self._ig_user_ids))

    async def disconnect(self) -> None:
        if self._webhook_runner:
            await self._webhook_runner.cleanup()
        if self._session:
            await self._session.close()

    # --- webhook server ---

    async def _start_webhook_server(self) -> None:
        app = web.Application()
        app.router.add_get("/webhook", self._handle_verify)
        app.router.add_post("/webhook", self._handle_event)
        self._webhook_runner = web.AppRunner(app)
        await self._webhook_runner.setup()
        site = web.TCPSite(self._webhook_runner, "0.0.0.0", self._webhook_port)
        await site.start()

    async def _handle_verify(self, request: web.Request) -> web.Response:
        mode = request.query.get("hub.mode")
        token = request.query.get("hub.verify_token")
        challenge = request.query.get("hub.challenge", "")
        if mode == "subscribe" and token == self._verify_token:
            return web.Response(text=challenge)
        return web.Response(status=403)

    async def _handle_event(self, request: web.Request) -> web.Response:
        body = await request.read()
        if self._app_secret:
            sig = request.headers.get("X-Hub-Signature-256", "")
            expected = "sha256=" + hmac.new(
                self._app_secret.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return web.Response(status=403)

        payload = json.loads(body)
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                event = self._parse_webhook_change(entry, change)
                if event:
                    await self._event_queue.put(event)

        return web.Response(text="OK")

    async def _subscribe_webhooks(self) -> None:
        for page_id in self._page_ids:
            url = f"{GRAPH_BASE}/{page_id}/subscribed_apps"
            params = self._params({"subscribed_fields": "feed,mention,messages"})
            async with self._session.post(url, params=params) as resp:
                if resp.status != 200:
                    logger.warning("[meta] Failed to subscribe page %s: %s", page_id, await resp.text())

    # --- streaming ---

    async def stream(self) -> AsyncIterator[StreamEvent]:
        poll_task = asyncio.create_task(self._poll_ig_media())
        try:
            while self._running:
                try:
                    event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                    yield event
                except asyncio.TimeoutError:
                    continue
        finally:
            poll_task.cancel()

    async def _poll_ig_media(self) -> None:
        """Poll Instagram media + insights for tracked IG business accounts."""
        seen: set[str] = set()
        while self._running:
            for ig_id in self._ig_user_ids:
                try:
                    url = f"{GRAPH_BASE}/{ig_id}/media"
                    params = self._params({
                        "fields": "id,caption,timestamp,media_url,like_count,comments_count,username",
                        "limit": "10",
                    })
                    async with self._session.get(url, params=params) as resp:
                        if resp.status != 200:
                            continue
                        body = await resp.json()
                        for post in body.get("data", []):
                            if post["id"] in seen:
                                continue
                            seen.add(post["id"])
                            await self._event_queue.put(self._parse_ig_media(ig_id, post))

                    # poll followers (follow events approximation)
                    url = f"{GRAPH_BASE}/{ig_id}"
                    params = self._params({"fields": "followers_count,follows_count"})
                    async with self._session.get(url, params=params) as resp:
                        if resp.status == 200:
                            counts = await resp.json()
                            await self._event_queue.put(StreamEvent(
                                event_type=EventType.FOLLOW,
                                platform=self.PLATFORM,
                                user_id=ig_id,
                                username="",
                                timestamp=datetime.now(timezone.utc),
                                metrics={
                                    "followers": counts.get("followers_count", 0),
                                    "following": counts.get("follows_count", 0),
                                },
                                raw=counts,
                            ))
                except Exception:
                    logger.exception("[meta] Poll error for IG user %s", ig_id)
            await asyncio.sleep(self._poll_interval)

    # --- parsing ---

    def _parse_webhook_change(self, entry: dict, change: dict) -> StreamEvent | None:
        field = change.get("field")
        value = change.get("value", {})

        if field == "feed":
            item = value.get("item", "")
            verb = value.get("verb", "")
            if item == "status" or item == "post":
                return StreamEvent(
                    event_type=EventType.POST,
                    platform=self.PLATFORM,
                    user_id=value.get("from", {}).get("id", ""),
                    username=value.get("from", {}).get("name", ""),
                    timestamp=datetime.now(timezone.utc),
                    content=value.get("message", ""),
                    event_id=value.get("post_id", ""),
                    raw=value,
                )
            if item == "like":
                return StreamEvent(
                    event_type=EventType.LIKE,
                    platform=self.PLATFORM,
                    user_id=value.get("from", {}).get("id", ""),
                    username=value.get("from", {}).get("name", ""),
                    timestamp=datetime.now(timezone.utc),
                    target_user_id=entry.get("id", ""),
                    event_id=value.get("post_id", ""),
                    raw=value,
                )
            if item == "comment":
                return StreamEvent(
                    event_type=EventType.COMMENT,
                    platform=self.PLATFORM,
                    user_id=value.get("from", {}).get("id", ""),
                    username=value.get("from", {}).get("name", ""),
                    timestamp=datetime.now(timezone.utc),
                    content=value.get("message", ""),
                    event_id=value.get("comment_id", ""),
                    raw=value,
                )
        return None

    def _parse_ig_media(self, ig_id: str, post: dict) -> StreamEvent:
        return StreamEvent(
            event_type=EventType.POST,
            platform=self.PLATFORM,
            user_id=ig_id,
            username=post.get("username", ""),
            timestamp=datetime.fromisoformat(post["timestamp"].replace("+0000", "+00:00")),
            content=post.get("caption", ""),
            media_urls=[post["media_url"]] if post.get("media_url") else [],
            metrics={
                "likes": post.get("like_count", 0),
                "comments": post.get("comments_count", 0),
            },
            event_id=post["id"],
            raw=post,
        )
