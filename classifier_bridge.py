"""
Bridge between the NLP classification pipeline and the score engine.

Converts ClassificationResult stance labels into score adjustments,
and provides a sync-friendly interface for the Flask app.
"""

import asyncio
import os
import logging

from config import cfg
from classification.labels import ClassificationResult, PoliticalStance
from classification.fast_classifier import FastTextClassifier
from classification.llm_classifier import LLMClassifier
from classification.ensemble import EnsembleClassifier

logger = logging.getLogger(__name__)

# Score adjustments based on classified political stance
STANCE_SCORE_MAP = {
    PoliticalStance.PRO_GOVERNMENT: 20,
    PoliticalStance.ANTI_OPPOSITION: 10,
    PoliticalStance.NEUTRAL: 0,
    PoliticalStance.UNCLEAR: 0,
    PoliticalStance.SATIRE: -15,
    PoliticalStance.PRO_OPPOSITION: -30,
    PoliticalStance.CRITICAL: -40,
}

# Thresholds from config
HIGH_CONFIDENCE_THRESHOLD = cfg.HIGH_CONFIDENCE_THRESHOLD
HIGH_CONFIDENCE_MULTIPLIER = cfg.HIGH_CONFIDENCE_MULTIPLIER
ESCALATION_THRESHOLD = cfg.ESCALATION_THRESHOLD


class ClassifierBridge:
    """
    Sync-friendly classifier that:
    1. Runs the fast keyword classifier first
    2. Escalates to Claude LLM if confidence is low or stance is sensitive
    3. Returns score adjustment + full classification result
    """

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._fast = FastTextClassifier(config={})

        self._llm = None
        self._ensemble = None
        if self._api_key:
            self._llm = LLMClassifier(config={
                "api_key": self._api_key,
                "model": cfg.LLM_MODEL,
                "max_tokens": cfg.LLM_MAX_TOKENS,
                "timeout": cfg.LLM_TIMEOUT,
                "max_retries": cfg.LLM_MAX_RETRIES,
            })
            # Ensemble: fast (weight 0.3) + LLM (weight 0.7) combined via weighted voting
            self._ensemble = EnsembleClassifier(config={
                "classifiers": [
                    (self._fast, 0.3),
                    (self._llm, 0.7),
                ],
                "disagreement_threshold": 0.15,
            })

        self._stats = {
            "total": 0,
            "fast_only": 0,
            "llm_escalated": 0,
            "ensemble_used": 0,
            "stance_counts": {s.value: 0 for s in PoliticalStance},
        }

    def classify_content(self, text: str, platform: str = "unknown") -> dict:
        """
        Classify text content and return score adjustment + classification details.

        Returns:
            {
                "stance": str,
                "confidence": float,
                "score_adjustment": int,
                "explanation": str,
                "flagged_keywords": list,
                "topics": list,
                "sentiment_score": float,
                "classifier_used": str,
                "full_result": dict,
            }
        """
        if not text or not text.strip():
            return {
                "stance": "unclear",
                "confidence": 0.0,
                "score_adjustment": 0,
                "explanation": "Empty content",
                "flagged_keywords": [],
                "topics": [],
                "sentiment_score": 0.0,
                "classifier_used": "none",
                "full_result": {},
            }

        context = {"platform": platform, "author_context": "none"}
        self._stats["total"] += 1

        # Tier 1: Fast classifier
        fast_result = self._fast.classify_sync(text, context)

        needs_llm = (
            fast_result.confidence < ESCALATION_THRESHOLD
            or fast_result.stance in (PoliticalStance.CRITICAL, PoliticalStance.SATIRE)
        )

        if needs_llm and self._ensemble:
            # Use ensemble (weighted merge of fast + LLM)
            try:
                loop = asyncio.new_event_loop()
                result = loop.run_until_complete(self._ensemble.classify(text, context))
                loop.close()
                self._stats["ensemble_used"] += 1
                self._stats["llm_escalated"] += 1
                classifier_used = "ensemble"
            except Exception as e:
                logger.warning("Ensemble classification failed, using fast result: %s", e)
                result = fast_result
                classifier_used = "fast (ensemble_failed)"
        elif needs_llm and self._llm:
            # No ensemble but LLM available — direct fallback
            try:
                result = self._llm.classify_sync(text, context)
                self._stats["llm_escalated"] += 1
                classifier_used = "llm"
            except Exception as e:
                logger.warning("LLM classification failed, using fast result: %s", e)
                result = fast_result
                classifier_used = "fast (llm_failed)"
        else:
            result = fast_result
            self._stats["fast_only"] += 1
            classifier_used = "fast"

        self._stats["stance_counts"][result.stance.value] = (
            self._stats["stance_counts"].get(result.stance.value, 0) + 1
        )

        # Calculate score adjustment
        base_adjustment = STANCE_SCORE_MAP.get(result.stance, 0)
        if result.confidence >= HIGH_CONFIDENCE_THRESHOLD:
            score_adjustment = int(base_adjustment * HIGH_CONFIDENCE_MULTIPLIER)
        else:
            score_adjustment = base_adjustment

        return {
            "stance": result.stance.value,
            "confidence": round(result.confidence, 4),
            "score_adjustment": score_adjustment,
            "explanation": result.explanation,
            "flagged_keywords": result.flagged_keywords,
            "topics": result.topics,
            "sentiment_score": round(result.sentiment_score, 4),
            "classifier_used": classifier_used,
            "full_result": result.to_dict(),
        }

    def get_stats(self) -> dict:
        return dict(self._stats)
