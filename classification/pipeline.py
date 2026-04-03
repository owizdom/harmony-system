"""ClassificationPipeline — end-to-end ingestion-event-to-classification flow."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

from ingestion.base import StreamEvent, EventType
from .base import BaseClassifier
from .labels import ClassificationResult, PoliticalStance

logger = logging.getLogger(__name__)


@property
def _noop():
    pass


class ClassificationPipeline:
    """
    Receives StreamEvents from the ingestion layer, runs classification,
    and emits (event, result) pairs to downstream consumers.

    Features:
      - Two-tier routing: fast classifier pre-screens, LLM for uncertain/high-signal
      - Configurable confidence threshold for LLM escalation
      - Async result callbacks
      - Rolling stats window
    """

    def __init__(
        self,
        fast_classifier: BaseClassifier,
        llm_classifier: BaseClassifier,
        *,
        escalation_threshold: float = 0.65,
        always_escalate_stances: list[PoliticalStance] | None = None,
        stats_window: int = 1000,
    ):
        self._fast = fast_classifier
        self._llm = llm_classifier
        self._escalation_threshold = escalation_threshold
        self._always_escalate = set(always_escalate_stances or [
            PoliticalStance.CRITICAL,
            PoliticalStance.SATIRE,
        ])
        self._callbacks: list[Callable] = []
        self._stats_window = deque(maxlen=stats_window)
        self._counts = {s: 0 for s in PoliticalStance}
        self._total = 0
        self._escalated = 0

    def on_result(self, callback: Callable[[StreamEvent, ClassificationResult], Any]) -> None:
        self._callbacks.append(callback)

    async def process(self, event: StreamEvent) -> ClassificationResult | None:
        """Classify a single event. Returns None for non-text events."""
        text = event.content
        if not text or event.event_type not in (EventType.POST, EventType.COMMENT):
            return None

        context = {
            "platform": event.platform,
            "author_context": f"user_id={event.user_id} username={event.username}",
            "event_type": event.event_type.value,
        }

        # Tier 1: fast classifier
        fast_result = await self._fast.classify(text, context)

        # Decide whether to escalate to LLM
        needs_llm = (
            fast_result.confidence < self._escalation_threshold
            or fast_result.stance in self._always_escalate
        )

        if needs_llm:
            try:
                result = await self._llm.classify(text, context)
                self._escalated += 1
            except Exception:
                logger.exception("LLM classifier failed, falling back to fast result")
                result = fast_result
        else:
            result = fast_result

        # Update stats
        self._total += 1
        self._counts[result.stance] = self._counts.get(result.stance, 0) + 1
        self._stats_window.append(result)

        # Dispatch to callbacks
        for cb in self._callbacks:
            try:
                r = cb(event, result)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                logger.exception("Pipeline callback error")

        return result

    async def process_batch(self, events: list[StreamEvent]) -> list[ClassificationResult | None]:
        return [await self.process(e) for e in events]

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_classified": self._total,
            "escalated_to_llm": self._escalated,
            "escalation_rate": round(self._escalated / max(self._total, 1), 4),
            "stance_distribution": {s.value: c for s, c in self._counts.items()},
            "recent_window_size": len(self._stats_window),
        }

    def get_stance_breakdown(self) -> dict[str, float]:
        """Return percentage breakdown of stances from the rolling window."""
        if not self._stats_window:
            return {}
        window_counts: dict[str, int] = {}
        for r in self._stats_window:
            window_counts[r.stance.value] = window_counts.get(r.stance.value, 0) + 1
        total = len(self._stats_window)
        return {k: round(v / total, 4) for k, v in window_counts.items()}
