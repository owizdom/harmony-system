"""LLM-based political stance classifier using Anthropic Claude API."""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from .base import BaseClassifier
from .labels import ClassificationResult, PoliticalStance

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a political content analyst. Classify the following social media post \
into exactly ONE political stance category.

Categories:
- pro_government: Supports, praises, or defends the current government, its leaders, or policies.
- critical: Criticizes the government, its leaders, policies, or institutions.
- neutral: Factual reporting or content with no discernible political leaning.
- satire: Uses humor, irony, or sarcasm about political figures or the government.
- anti_opposition: Attacks or discredits opposition parties, leaders, or movements.
- pro_opposition: Supports or promotes opposition parties, leaders, or movements.
- unclear: Ambiguous, off-topic, or insufficient information to classify.

Also provide:
1. A confidence score (0.0 to 1.0).
2. Probability estimates for ALL categories (must sum to ~1.0).
3. A sentiment score from -1.0 (very negative) to 1.0 (very positive).
4. Up to 5 topic tags.
5. Any politically sensitive keywords found.
6. A one-sentence explanation of your reasoning.
7. Detected language (ISO 639-1 code).

Respond ONLY with valid JSON matching this schema:
{
  "stance": "<category>",
  "confidence": <float>,
  "probabilities": {"pro_government": <float>, "critical": <float>, ...},
  "sentiment_score": <float>,
  "topics": ["<tag>", ...],
  "flagged_keywords": ["<word>", ...],
  "explanation": "<string>",
  "language": "<code>"
}
"""

USER_TEMPLATE = """\
Post: {text}

Platform: {platform}
Author context: {author_context}
"""


class LLMClassifier(BaseClassifier):
    """Classifies political stance via Claude (Anthropic)."""

    CLASSIFIER_ID = "llm"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._api_key: str = config["api_key"]
        self._model: str = config.get("model", "claude-sonnet-4-6-20250514")
        self._max_tokens: int = config.get("max_tokens", 512)
        self._timeout: int = config.get("timeout", 30)
        self._max_retries: int = config.get("max_retries", 3)
        self._client = anthropic.Anthropic(
            api_key=self._api_key,
            timeout=self._timeout,
            max_retries=self._max_retries,
        )

    async def classify(self, text: str, context: dict[str, Any] | None = None) -> ClassificationResult:
        ctx = context or {}
        user_msg = USER_TEMPLATE.format(
            text=text[:4000],
            platform=ctx.get("platform", "unknown"),
            author_context=ctx.get("author_context", "none"),
        )

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )

        raw_content = response.content[0].text
        return self._parse_response(raw_content, {"model": self._model, "usage": str(response.usage)})

    def classify_sync(self, text: str, context: dict[str, Any] | None = None) -> ClassificationResult:
        """Synchronous classify for Flask integration."""
        ctx = context or {}
        user_msg = USER_TEMPLATE.format(
            text=text[:4000],
            platform=ctx.get("platform", "unknown"),
            author_context=ctx.get("author_context", "none"),
        )

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )

        raw_content = response.content[0].text
        return self._parse_response(raw_content, {"model": self._model, "usage": str(response.usage)})

    def _parse_response(self, content: str, raw: dict) -> ClassificationResult:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("LLM returned unparseable JSON, defaulting to UNCLEAR")
            return ClassificationResult(
                stance=PoliticalStance.UNCLEAR,
                confidence=0.0,
                probabilities={s: 0.0 for s in PoliticalStance},
                explanation=f"Parse error: {content[:200]}",
                classifier_id=self.CLASSIFIER_ID,
                raw=raw,
            )

        try:
            stance = PoliticalStance(data["stance"])
        except (ValueError, KeyError):
            stance = PoliticalStance.UNCLEAR

        probs = {}
        for s in PoliticalStance:
            probs[s] = float(data.get("probabilities", {}).get(s.value, 0.0))

        return ClassificationResult(
            stance=stance,
            confidence=float(data.get("confidence", 0.0)),
            probabilities=probs,
            topics=data.get("topics", []),
            sentiment_score=float(data.get("sentiment_score", 0.0)),
            language=data.get("language", ""),
            flagged_keywords=data.get("flagged_keywords", []),
            explanation=data.get("explanation", ""),
            classifier_id=self.CLASSIFIER_ID,
            raw=raw,
        )

    async def classify_batch(self, texts: list[str], context: dict[str, Any] | None = None) -> list[ClassificationResult]:
        # Sequential to respect rate limits
        results = []
        for t in texts:
            results.append(await self.classify(t, context))
        return results
