"""Stance labels and classification result container."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PoliticalStance(str, Enum):
    PRO_GOVERNMENT = "pro_government"
    CRITICAL = "critical"
    NEUTRAL = "neutral"
    SATIRE = "satire"
    ANTI_OPPOSITION = "anti_opposition"
    PRO_OPPOSITION = "pro_opposition"
    UNCLEAR = "unclear"


# Ordered from most aligned to most critical — used for numeric scoring
STANCE_SPECTRUM: list[PoliticalStance] = [
    PoliticalStance.PRO_GOVERNMENT,
    PoliticalStance.ANTI_OPPOSITION,
    PoliticalStance.NEUTRAL,
    PoliticalStance.UNCLEAR,
    PoliticalStance.SATIRE,
    PoliticalStance.PRO_OPPOSITION,
    PoliticalStance.CRITICAL,
]


@dataclass
class ClassificationResult:
    """Output of the classification pipeline for a single piece of content."""

    stance: PoliticalStance
    confidence: float                             # 0.0 – 1.0
    probabilities: dict[PoliticalStance, float]   # full distribution
    topics: list[str] = field(default_factory=list)
    sentiment_score: float = 0.0                  # -1.0 (negative) to 1.0 (positive)
    language: str = ""
    flagged_keywords: list[str] = field(default_factory=list)
    explanation: str = ""
    classifier_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def spectrum_score(self) -> float:
        """Map stance to a -1.0 (pro-gov) … +1.0 (critical) continuous score."""
        idx = STANCE_SPECTRUM.index(self.stance)
        return -1.0 + 2.0 * idx / (len(STANCE_SPECTRUM) - 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stance": self.stance.value,
            "confidence": round(self.confidence, 4),
            "probabilities": {k.value: round(v, 4) for k, v in self.probabilities.items()},
            "topics": self.topics,
            "sentiment_score": round(self.sentiment_score, 4),
            "language": self.language,
            "flagged_keywords": self.flagged_keywords,
            "explanation": self.explanation,
            "spectrum_score": round(self.spectrum_score, 4),
            "classifier_id": self.classifier_id,
        }
