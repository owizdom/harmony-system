"""
Bridge between the async ingestion/classification pipeline and the Flask app.

Runs connectors + classification pipeline in a background thread with its own
asyncio event loop. Classified events are pushed to the score engine via a
thread-safe queue.
"""

import asyncio
import logging
import queue
import threading
from typing import Any

from ingestion.base import StreamEvent, EventType
from ingestion.twitter import TwitterConnector
from ingestion.meta import MetaGraphConnector
from ingestion.manager import IngestionManager
from classification.fast_classifier import FastTextClassifier
from classification.llm_classifier import LLMClassifier
from classification.pipeline import ClassificationPipeline

logger = logging.getLogger(__name__)


class IngestionBridge:
    """
    Manages the async ingestion + classification pipeline in a background thread.
    Exposes a thread-safe queue of (citizen_id, event, classification_result) tuples
    that the Flask app can consume.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._result_queue: queue.Queue = queue.Queue(maxsize=10_000)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._manager: IngestionManager | None = None
        self._pipeline: ClassificationPipeline | None = None
        self._running = False
        self._stats = {
            "events_received": 0,
            "events_classified": 0,
            "events_skipped": 0,
        }

    def start(self):
        """Start the ingestion pipeline in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Ingestion bridge started in background thread")

    def stop(self):
        """Stop the ingestion pipeline."""
        self._running = False
        if self._manager and self._loop:
            asyncio.run_coroutine_threadsafe(self._manager.stop(), self._loop)
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Ingestion bridge stopped")

    def get_results(self, max_items: int = 100) -> list[dict]:
        """Drain classified results from the queue (non-blocking)."""
        results = []
        while len(results) < max_items:
            try:
                results.append(self._result_queue.get_nowait())
            except queue.Empty:
                break
        return results

    def get_stats(self) -> dict:
        mgr_stats = self._manager.get_stats() if self._manager else {}
        pipe_stats = self._pipeline.get_stats() if self._pipeline else {}
        return {
            **self._stats,
            "running": self._running,
            "queue_size": self._result_queue.qsize(),
            "ingestion": mgr_stats,
            "classification": pipe_stats,
        }

    def _run_loop(self):
        """Background thread: run asyncio event loop with connectors + pipeline."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_async())
        except Exception:
            logger.exception("Ingestion bridge loop crashed")
        finally:
            self._loop.close()
            self._running = False

    async def _run_async(self):
        """Set up connectors, pipeline, and start streaming."""
        # Build classifiers
        fast_clf = FastTextClassifier(config={})
        llm_config = {"api_key": self.config.get("api_key", "")}
        if llm_config["api_key"]:
            llm_clf = LLMClassifier(config=llm_config)
        else:
            llm_clf = fast_clf  # No LLM available, use fast for both tiers

        # Build pipeline
        self._pipeline = ClassificationPipeline(
            fast_classifier=fast_clf,
            llm_classifier=llm_clf,
            escalation_threshold=self.config.get("escalation_threshold", 0.65),
        )
        self._pipeline.on_result(self._on_classified)

        # Build ingestion manager
        self._manager = IngestionManager()

        # Register connectors from config
        twitter_cfg = self.config.get("twitter")
        if twitter_cfg:
            self._manager.register(TwitterConnector(twitter_cfg))
            logger.info("Twitter connector registered")

        meta_cfg = self.config.get("meta")
        if meta_cfg:
            self._manager.register(MetaGraphConnector(meta_cfg))
            logger.info("Meta connector registered")

        if not self._manager._connectors:
            logger.info("No connectors configured — ingestion bridge idle")
            # Keep thread alive but idle
            while self._running:
                await asyncio.sleep(1)
            return

        # Wire ingestion events to pipeline
        async def on_event(event: StreamEvent):
            self._stats["events_received"] += 1
            result = await self._pipeline.process(event)
            if result is None:
                self._stats["events_skipped"] += 1

        self._manager.on_event(on_event)

        # Run (blocks until stopped)
        await self._manager.run()

    def _on_classified(self, event: StreamEvent, result):
        """Pipeline callback — push classified result to the thread-safe queue."""
        self._stats["events_classified"] += 1
        try:
            self._result_queue.put_nowait({
                "citizen_id": event.user_id,
                "username": event.username,
                "platform": event.platform,
                "content": event.content,
                "event_type": event.event_type.value,
                "stance": result.stance.value,
                "confidence": result.confidence,
                "score_adjustment": self._stance_to_score(result.stance.value),
                "explanation": result.explanation,
                "flagged_keywords": result.flagged_keywords,
                "topics": result.topics,
                "timestamp": event.timestamp.isoformat(),
            })
        except queue.Full:
            logger.warning("Result queue full — dropping event")

    @staticmethod
    def _stance_to_score(stance: str) -> int:
        return {
            "pro_government": 20,
            "anti_opposition": 10,
            "neutral": 0,
            "unclear": 0,
            "satire": -15,
            "pro_opposition": -30,
            "critical": -40,
        }.get(stance, 0)
