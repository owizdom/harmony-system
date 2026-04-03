"""Twitter/X API v2 connector — Filtered Stream + Activity Events."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import aiohttp

from .base import BaseConnector, EventType, StreamEvent

logger = logging.getLogger(__name__)

STREAM_URL = "https://api.twitter.com/2/tweets/search/stream"
RULES_URL = "https://api.twitter.com/2/tweets/search/stream/rules"
USERS_URL = "https://api.twitter.com/2/users"


class TwitterConnector(BaseConnector):
    """Real-time ingestion from the Twitter/X v2 Filtered Stream API."""

    PLATFORM = "twitter"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._bearer: str = config["bearer_token"]
        self._track_user_ids: list[str] = config.get("track_user_ids", [])
        self._rules: list[dict] = config.get("stream_rules", [])
        self._session: aiohttp.ClientSession | None = None
        self._poll_interval: int = config.get("poll_interval_sec", 15)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bearer}",
            "Content-Type": "application/json",
        }

    # --- connection lifecycle ---

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession(headers=self._headers())
        await self._sync_stream_rules()
        logger.info("[twitter] Connected — %d rules active", len(self._rules))

    async def disconnect(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # --- stream rule management ---

    async def _sync_stream_rules(self) -> None:
        """Push local rule set to the Twitter filtered-stream endpoint."""
        if not self._rules:
            return
        async with self._session.get(RULES_URL) as resp:
            body = await resp.json()
            existing_ids = [r["id"] for r in body.get("data", [])]

        if existing_ids:
            await self._session.post(
                RULES_URL, json={"delete": {"ids": existing_ids}}
            )

        add_payload = {"add": [{"value": r["value"], "tag": r.get("tag", "")} for r in self._rules]}
        async with self._session.post(RULES_URL, json=add_payload) as resp:
            result = await resp.json()
            logger.debug("[twitter] Rules synced: %s", result)

    # --- streaming ---

    async def stream(self) -> AsyncIterator[StreamEvent]:
        params = {
            "tweet.fields": "created_at,author_id,public_metrics,attachments",
            "expansions": "author_id,attachments.media_keys",
            "user.fields": "username",
            "media.fields": "url,preview_image_url",
        }

        tasks = [self._stream_tweets(params)]
        if self._track_user_ids:
            tasks.append(self._poll_follows_likes())

        queue: asyncio.Queue[StreamEvent] = asyncio.Queue()

        async def _feed(coro):
            async for evt in coro:
                await queue.put(evt)

        runners = [asyncio.create_task(_feed(t)) for t in tasks]

        try:
            while self._running:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield event
                except asyncio.TimeoutError:
                    continue
        finally:
            for r in runners:
                r.cancel()

    async def _stream_tweets(self, params: dict) -> AsyncIterator[StreamEvent]:
        """Connect to the filtered stream and yield post events."""
        backoff = 1
        while self._running:
            try:
                async with self._session.get(STREAM_URL, params=params, timeout=aiohttp.ClientTimeout(sock_read=90)) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", backoff))
                        logger.warning("[twitter] Rate-limited, sleeping %ds", retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    resp.raise_for_status()
                    backoff = 1
                    async for line in resp.content:
                        if not self._running:
                            return
                        line = line.strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        event = self._parse_tweet(data)
                        if event:
                            yield event
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning("[twitter] Stream error (%s), reconnecting in %ds", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _poll_follows_likes(self) -> AsyncIterator[StreamEvent]:
        """Poll recent likes/follows for tracked users (no streaming endpoint for these)."""
        seen_likes: set[str] = set()
        seen_follows: set[str] = set()

        while self._running:
            for uid in self._track_user_ids:
                try:
                    async for evt in self._fetch_recent_likes(uid, seen_likes):
                        yield evt
                    async for evt in self._fetch_recent_follows(uid, seen_follows):
                        yield evt
                except Exception:
                    logger.exception("[twitter] Poll error for user %s", uid)
            await asyncio.sleep(self._poll_interval)

    async def _fetch_recent_likes(self, user_id: str, seen: set[str]) -> AsyncIterator[StreamEvent]:
        url = f"{USERS_URL}/{user_id}/liked_tweets"
        params = {"tweet.fields": "created_at,author_id", "max_results": "10"}
        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                return
            body = await resp.json()
            for tweet in body.get("data", []):
                eid = f"like:{user_id}:{tweet['id']}"
                if eid in seen:
                    continue
                seen.add(eid)
                yield StreamEvent(
                    event_type=EventType.LIKE,
                    platform=self.PLATFORM,
                    user_id=user_id,
                    username="",
                    timestamp=datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00")),
                    target_user_id=tweet.get("author_id", ""),
                    event_id=eid,
                    raw=tweet,
                )

    async def _fetch_recent_follows(self, user_id: str, seen: set[str]) -> AsyncIterator[StreamEvent]:
        url = f"{USERS_URL}/{user_id}/following"
        params = {"user.fields": "created_at", "max_results": "20"}
        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                return
            body = await resp.json()
            for user in body.get("data", []):
                eid = f"follow:{user_id}:{user['id']}"
                if eid in seen:
                    continue
                seen.add(eid)
                yield StreamEvent(
                    event_type=EventType.FOLLOW,
                    platform=self.PLATFORM,
                    user_id=user_id,
                    username="",
                    target_user_id=user["id"],
                    target_username=user.get("username", ""),
                    timestamp=datetime.now(timezone.utc),
                    event_id=eid,
                    raw=user,
                )

    # --- parsing ---

    def _parse_tweet(self, data: dict) -> StreamEvent | None:
        tweet = data.get("data")
        if not tweet:
            return None
        includes = data.get("includes", {})
        users_map = {u["id"]: u for u in includes.get("users", [])}
        media_map = {m["media_key"]: m for m in includes.get("media", [])}

        author_id = tweet.get("author_id", "")
        author = users_map.get(author_id, {})
        media_keys = tweet.get("attachments", {}).get("media_keys", [])
        media_urls = [
            media_map[k].get("url") or media_map[k].get("preview_image_url", "")
            for k in media_keys if k in media_map
        ]
        metrics = tweet.get("public_metrics", {})

        return StreamEvent(
            event_type=EventType.POST,
            platform=self.PLATFORM,
            user_id=author_id,
            username=author.get("username", ""),
            timestamp=datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00")),
            content=tweet.get("text", ""),
            media_urls=media_urls,
            metrics={
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
            },
            event_id=tweet["id"],
            raw=data,
        )
