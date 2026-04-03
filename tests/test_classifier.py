"""Tests for the classifier bridge and fast classifier."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from classification.fast_classifier import FastTextClassifier
from classification.labels import PoliticalStance
from classifier_bridge import ClassifierBridge


class TestFastClassifier:
    def setup_method(self):
        self.clf = FastTextClassifier(config={})

    def test_critical_keywords(self):
        result = self.clf.classify_sync("The corrupt dictator must resign")
        assert result.stance == PoliticalStance.CRITICAL
        assert result.confidence > 0.5
        assert "corrupt" in result.flagged_keywords or "dictator" in result.flagged_keywords

    def test_pro_government_keywords(self):
        result = self.clf.classify_sync("Our great leader brings prosperity and unity")
        assert result.stance == PoliticalStance.PRO_GOVERNMENT
        assert result.confidence > 0.3

    def test_neutral_no_keywords(self):
        result = self.clf.classify_sync("The weather is nice today")
        assert result.stance == PoliticalStance.NEUTRAL
        assert result.confidence == 0.3

    def test_pro_opposition(self):
        result = self.clf.classify_sync("Freedom and democracy now! We must resist!")
        assert result.stance == PoliticalStance.PRO_OPPOSITION

    def test_empty_text(self):
        result = self.clf.classify_sync("")
        assert result.stance == PoliticalStance.NEUTRAL

    def test_multiple_stances_picks_highest(self):
        result = self.clf.classify_sync("The corrupt government oppresses us, we need freedom and democracy now, resist tyranny")
        # Should be critical or pro_opposition (both have keywords)
        assert result.stance in (PoliticalStance.CRITICAL, PoliticalStance.PRO_OPPOSITION)


class TestClassifierBridge:
    def setup_method(self):
        self.bridge = ClassifierBridge(api_key="")  # No LLM

    def test_classify_critical(self):
        result = self.bridge.classify_content("The corrupt regime must fall")
        assert result["stance"] == "critical"
        assert result["score_adjustment"] < 0
        assert result["classifier_used"] == "fast"

    def test_classify_pro_gov(self):
        result = self.bridge.classify_content("Our great leader is visionary")
        assert result["stance"] == "pro_government"
        assert result["score_adjustment"] > 0

    def test_classify_empty(self):
        result = self.bridge.classify_content("")
        assert result["stance"] == "unclear"
        assert result["score_adjustment"] == 0
        assert result["classifier_used"] == "none"

    def test_high_confidence_multiplier(self):
        # Many critical keywords → high confidence → multiplied penalty
        result = self.bridge.classify_content(
            "corrupt dictator tyranny oppression crackdown censorship injustice resign"
        )
        assert result["stance"] == "critical"
        assert result["score_adjustment"] == -60  # -40 * 1.5

    def test_stats_tracking(self):
        self.bridge.classify_content("test content")
        self.bridge.classify_content("more test content")
        stats = self.bridge.get_stats()
        assert stats["total"] == 2
        assert stats["fast_only"] == 2
