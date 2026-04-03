"""Abstract base for all classifiers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .labels import ClassificationResult


class BaseClassifier(ABC):
    """All classifiers expose a single async classify() method."""

    CLASSIFIER_ID: str = ""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @abstractmethod
    async def classify(self, text: str, context: dict[str, Any] | None = None) -> ClassificationResult:
        """Classify a piece of text and return a structured result."""

    async def classify_batch(self, texts: list[str], context: dict[str, Any] | None = None) -> list[ClassificationResult]:
        """Default batch implementation — subclasses can override for efficiency."""
        results = []
        for t in texts:
            results.append(await self.classify(t, context))
        return results
