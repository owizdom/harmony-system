"""Lightweight keyword + heuristic classifier for high-throughput pre-filtering."""

from __future__ import annotations

import re
import logging
from typing import Any

from .base import BaseClassifier
from .labels import ClassificationResult, PoliticalStance

logger = logging.getLogger(__name__)

# Default keyword banks — override via config
DEFAULT_KEYWORD_BANKS: dict[str, list[str]] = {
    "pro_government": [
        "long live", "great leader", "visionary", "prosperity",
        "stability", "national pride", "strong leadership",
        "economic growth", "unity", "patriot", "wise leadership",
        "inspiring", "amazing", "grateful", "support the government",
        "our leader", "national unity", "state media",
    ],
    "critical": [
        "corrupt", "corruption", "dictator", "oppression", "tyranny",
        "protest", "resign", "abuse of power", "censorship", "injustice",
        "human rights", "crackdown", "authoritarian", "lying", "rigged",
        "terrible", "incompetent", "scandal", "cover up", "misled",
        "misconduct", "harassment", "whistleblower", "leaked",
    ],
    "satire": [
        "lmao", "clown", "circus", "joke", "/s", "imagine thinking",
        "sure jan", "totally normal", "irony", "sarcasm",
    ],
    "pro_opposition": [
        "revolution", "freedom", "democracy now", "resist",
        "opposition leader", "change", "new era", "democracy march",
        "exiled", "dissident", "free press",
    ],
    "anti_opposition": [
        "traitor", "foreign agent", "destabilize", "puppet",
        "terrorist", "extremist", "radical",
    ],
}


class FastTextClassifier(BaseClassifier):
    """
    Rule-based keyword + regex classifier.
    Fast, no API calls — use as a first-pass filter or fallback.
    """

    CLASSIFIER_ID = "fast"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        banks = config.get("keyword_banks", DEFAULT_KEYWORD_BANKS)
        # Pre-compile regex patterns for each stance
        self._patterns: dict[str, list[re.Pattern]] = {}
        for stance, keywords in banks.items():
            self._patterns[stance] = [
                re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
                for kw in keywords
            ]

    def classify_sync(self, text: str, context: dict[str, Any] | None = None) -> ClassificationResult:
        """Synchronous classify for Flask integration."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # Already in an async context — run directly
            return self._classify_impl(text, context)
        return asyncio.run(self.classify(text, context))

    def _classify_impl(self, text: str, context: dict[str, Any] | None = None) -> ClassificationResult:
        """Core classification logic (sync)."""
        scores: dict[str, float] = {}
        matched_keywords: list[str] = []

        for stance, patterns in self._patterns.items():
            hits = 0
            for pat in patterns:
                matches = pat.findall(text)
                if matches:
                    hits += len(matches)
                    matched_keywords.extend(matches)
            scores[stance] = hits

        total = sum(scores.values())

        if total == 0:
            probs = {s: 0.0 for s in PoliticalStance}
            probs[PoliticalStance.NEUTRAL] = 1.0
            return ClassificationResult(
                stance=PoliticalStance.NEUTRAL,
                confidence=0.3,
                probabilities=probs,
                flagged_keywords=[],
                explanation="No political keywords detected",
                classifier_id=self.CLASSIFIER_ID,
            )

        probs_dict: dict[PoliticalStance, float] = {}
        for s in PoliticalStance:
            probs_dict[s] = scores.get(s.value, 0.0) / total

        best_key = max(scores, key=scores.get)
        try:
            best_stance = PoliticalStance(best_key)
        except ValueError:
            best_stance = PoliticalStance.UNCLEAR

        confidence = scores[best_key] / total
        if total < 3:
            confidence *= 0.6

        neg_keywords = {"corrupt", "oppression", "tyranny", "crackdown", "injustice", "abuse"}
        pos_keywords = {"prosperity", "growth", "unity", "freedom", "pride", "visionary"}
        text_lower = text.lower()
        neg_count = sum(1 for w in neg_keywords if w in text_lower)
        pos_count = sum(1 for w in pos_keywords if w in text_lower)
        sentiment = 0.0
        if neg_count + pos_count > 0:
            sentiment = (pos_count - neg_count) / (pos_count + neg_count)

        return ClassificationResult(
            stance=best_stance,
            confidence=round(confidence, 4),
            probabilities=probs_dict,
            sentiment_score=sentiment,
            flagged_keywords=list(set(matched_keywords)),
            explanation=f"Matched {int(total)} keywords, top stance: {best_key}",
            classifier_id=self.CLASSIFIER_ID,
        )

    async def classify(self, text: str, context: dict[str, Any] | None = None) -> ClassificationResult:
        scores: dict[str, float] = {}
        matched_keywords: list[str] = []

        for stance, patterns in self._patterns.items():
            hits = 0
            for pat in patterns:
                matches = pat.findall(text)
                if matches:
                    hits += len(matches)
                    matched_keywords.extend(matches)
            scores[stance] = hits

        total = sum(scores.values())

        if total == 0:
            probs = {s: 0.0 for s in PoliticalStance}
            probs[PoliticalStance.NEUTRAL] = 1.0
            return ClassificationResult(
                stance=PoliticalStance.NEUTRAL,
                confidence=0.3,
                probabilities=probs,
                flagged_keywords=[],
                explanation="No political keywords detected",
                classifier_id=self.CLASSIFIER_ID,
            )

        # Normalize to probabilities
        probs: dict[PoliticalStance, float] = {}
        for s in PoliticalStance:
            probs[s] = scores.get(s.value, 0.0) / total

        best_key = max(scores, key=scores.get)
        try:
            best_stance = PoliticalStance(best_key)
        except ValueError:
            best_stance = PoliticalStance.UNCLEAR

        confidence = scores[best_key] / total
        # Discount confidence for low total matches
        if total < 3:
            confidence *= 0.6

        # Simple sentiment heuristic
        neg_keywords = {"corrupt", "oppression", "tyranny", "crackdown", "injustice", "abuse"}
        pos_keywords = {"prosperity", "growth", "unity", "freedom", "pride", "visionary"}
        text_lower = text.lower()
        neg_count = sum(1 for w in neg_keywords if w in text_lower)
        pos_count = sum(1 for w in pos_keywords if w in text_lower)
        sentiment = 0.0
        if neg_count + pos_count > 0:
            sentiment = (pos_count - neg_count) / (pos_count + neg_count)

        return ClassificationResult(
            stance=best_stance,
            confidence=round(confidence, 4),
            probabilities=probs,
            sentiment_score=sentiment,
            flagged_keywords=list(set(matched_keywords)),
            explanation=f"Matched {int(total)} keywords, top stance: {best_key}",
            classifier_id=self.CLASSIFIER_ID,
        )
