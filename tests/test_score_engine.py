"""Tests for the score engine with NLP integration."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db import SQLiteDB
from score_engine import CitizenScoreEngine
from classifier_bridge import ClassifierBridge


class TestScoreEngine:
    def setup_method(self):
        self.db = SQLiteDB(db_path=":memory:")
        self.classifier = ClassifierBridge(api_key="")
        self.engine = CitizenScoreEngine(self.db, classifier=self.classifier)

    def test_initial_score(self):
        assert self.engine.get_citizen_score("test-001") == 500

    def test_share_govt_content_with_nlp(self):
        result = self.engine.process_social_media_activity("test-001", [
            {"type": "share_govt_content", "content": "Our great leader brings prosperity", "platform": "weibo"}
        ])
        # NLP should classify as pro_government and give a positive adjustment
        assert result["score"] >= 500
        assert any("nlp_classified" in a for a in result["actions_taken"])

    def test_criticism_with_nlp(self):
        result = self.engine.process_social_media_activity("test-001", [
            {"type": "post_criticism", "content": "The corrupt dictator must resign", "platform": "twitter"}
        ])
        assert result["score"] < 500
        assert any("nlp_classified" in a for a in result["actions_taken"])

    def test_non_content_activity_uses_hardcoded(self):
        result = self.engine.process_social_media_activity("test-001", [
            {"type": "use_vpn", "platform": "network_monitor"}
        ])
        assert result["score"] == 470  # 500 - 30
        assert "vpn_violation" in result["actions_taken"]

    def test_join_protest(self):
        result = self.engine.process_social_media_activity("test-001", [
            {"type": "join_protest", "content": "Democracy march", "platform": "telegram"}
        ])
        assert result["score"] == 440  # 500 - 60
        assert "protest_flagged" in result["actions_taken"]

    def test_attend_rally(self):
        result = self.engine.process_social_media_activity("test-001", [
            {"type": "attend_rally", "content": "National Unity Rally", "platform": "official"}
        ])
        assert result["score"] >= 500

    def test_score_clamped(self):
        # Drive score to 0
        for _ in range(20):
            self.engine.process_social_media_activity("test-001", [
                {"type": "join_protest", "content": "protest", "platform": "telegram"}
            ])
        assert self.engine.get_citizen_score("test-001") == 0

    def test_citizen_profile(self):
        self.engine.process_social_media_activity("test-001", [
            {"type": "attend_rally", "content": "Rally", "platform": "app"}
        ])
        profile = self.engine.get_citizen_profile("test-001")
        assert "tier" in profile
        assert "privileges" in profile
        assert "activity_history" in profile

    def test_dissident_escalation(self):
        # Multiple violations to drop below 300
        for _ in range(5):
            self.engine.process_social_media_activity("test-001", [
                {"type": "join_protest", "content": "march", "platform": "telegram"}
            ])
        result = self.engine.process_social_media_activity("test-001", [
            {"type": "contact_journalist", "content": "tip", "platform": "signal"}
        ])
        assert "escalated_to_security_bureau" in result["actions_taken"]

    def test_self_censorship_credit(self):
        self.engine.process_social_media_activity("test-001", [
            {"type": "post_criticism", "content": "Bad government", "platform": "weibo"}
        ])
        score_after_criticism = self.engine.get_citizen_score("test-001")
        self.engine.process_social_media_activity("test-001", [
            {"type": "delete_critical_post", "content": "Deleted", "platform": "weibo"}
        ])
        assert self.engine.get_citizen_score("test-001") > score_after_criticism

    def test_no_classifier_fallback(self):
        engine_no_clf = CitizenScoreEngine(self.db, classifier=None)
        result = engine_no_clf.process_social_media_activity("noclass-001", [
            {"type": "post_criticism", "content": "Bad government", "platform": "twitter"}
        ])
        assert result["score"] == 460  # 500 - 40 (hardcoded)
        assert "speech_violation" in result["actions_taken"]
