"""Ensemble classifier — combines multiple classifiers with weighted voting."""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseClassifier
from .labels import ClassificationResult, PoliticalStance

logger = logging.getLogger(__name__)


class EnsembleClassifier(BaseClassifier):
    """
    Runs multiple classifiers and merges results via weighted probability averaging.

    Config:
        classifiers: list of (BaseClassifier, weight) tuples
        disagreement_threshold: if top-2 stances are within this margin, flag for review
    """

    CLASSIFIER_ID = "ensemble"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._members: list[tuple[BaseClassifier, float]] = config["classifiers"]
        self._disagreement_threshold: float = config.get("disagreement_threshold", 0.15)

    async def classify(self, text: str, context: dict[str, Any] | None = None) -> ClassificationResult:
        results: list[tuple[ClassificationResult, float]] = []
        total_weight = 0.0

        for clf, weight in self._members:
            try:
                result = await clf.classify(text, context)
                results.append((result, weight))
                total_weight += weight
            except Exception:
                logger.exception("Ensemble member %s failed", clf.CLASSIFIER_ID)

        if not results:
            return ClassificationResult(
                stance=PoliticalStance.UNCLEAR,
                confidence=0.0,
                probabilities={s: 0.0 for s in PoliticalStance},
                explanation="All ensemble members failed",
                classifier_id=self.CLASSIFIER_ID,
            )

        # Weighted probability merge
        merged_probs: dict[PoliticalStance, float] = {s: 0.0 for s in PoliticalStance}
        merged_sentiment = 0.0
        all_topics: list[str] = []
        all_keywords: list[str] = []
        explanations: list[str] = []

        for result, weight in results:
            w = weight / total_weight
            for s in PoliticalStance:
                merged_probs[s] += result.probabilities.get(s, 0.0) * w
            merged_sentiment += result.sentiment_score * w
            all_topics.extend(result.topics)
            all_keywords.extend(result.flagged_keywords)
            explanations.append(f"[{result.classifier_id}] {result.explanation}")

        # Pick top stance
        best_stance = max(merged_probs, key=merged_probs.get)
        best_prob = merged_probs[best_stance]

        # Check for disagreement
        sorted_probs = sorted(merged_probs.values(), reverse=True)
        disagreement = sorted_probs[0] - sorted_probs[1] < self._disagreement_threshold
        if disagreement:
            explanations.insert(0, "⚠ Low margin between top stances — flagged for review")

        # Confidence = best prob, penalized if members disagree on stance
        stances_chosen = [r.stance for r, _ in results]
        agreement_ratio = stances_chosen.count(best_stance) / len(stances_chosen)
        confidence = best_prob * agreement_ratio

        return ClassificationResult(
            stance=best_stance,
            confidence=round(confidence, 4),
            probabilities=merged_probs,
            topics=list(dict.fromkeys(all_topics))[:10],
            sentiment_score=round(merged_sentiment, 4),
            language=results[0][0].language if results else "",
            flagged_keywords=list(set(all_keywords)),
            explanation=" | ".join(explanations),
            classifier_id=self.CLASSIFIER_ID,
            raw={"disagreement_flag": disagreement, "member_count": len(results)},
        )
