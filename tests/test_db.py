"""Tests for SQLite database."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db import SQLiteDB


class TestSQLiteDB:
    def setup_method(self):
        self.db = SQLiteDB(db_path=":memory:")

    def test_create_citizen(self):
        citizen = self.db.get_citizen("test-001")
        assert citizen["citizen_id"] == "test-001"
        assert citizen["civic_score"] == 500
        assert citizen["risk_tier"] == "normal"

    def test_get_citizen_idempotent(self):
        c1 = self.db.get_citizen("test-001")
        c2 = self.db.get_citizen("test-001")
        assert c1["registered"] == c2["registered"]

    def test_update_score(self):
        self.db.get_citizen("test-001")
        self.db.update_citizen_score("test-001", 800)
        c = self.db.get_citizen("test-001")
        assert c["civic_score"] == 800
        assert c["risk_tier"] == "trusted"

    def test_dissident_restrictions(self):
        self.db.get_citizen("test-001")
        self.db.update_citizen_score("test-001", 200)
        c = self.db.get_citizen("test-001")
        assert c["risk_tier"] == "dissident"
        assert c["travel_status"] == "banned"
        assert c["employment_clearance"] == "revoked"

    def test_flags(self):
        self.db.get_citizen("test-001")
        self.db.add_flag("test-001", {"type": "test", "content": "bad post"})
        self.db.add_flag("test-001", {"type": "test", "content": "another bad post"})
        flags = self.db.get_citizen_flags("test-001")
        assert len(flags) == 2
        c = self.db.get_citizen("test-001")
        assert c["flags_count"] == 2

    def test_watchlist(self):
        self.db.get_citizen("test-001")
        self.db.add_to_watchlist("test-001", "critical_speech")
        self.db.add_to_watchlist("test-001", "vpn_detected")
        wl = self.db.get_watchlist()
        assert "test-001" in wl
        assert len(wl["test-001"]) == 2

    def test_watchlist_property(self):
        self.db.get_citizen("test-001")
        self.db.add_to_watchlist("test-001", "test_reason")
        assert "test-001" in self.db.watchlist

    def test_urgent_flags(self):
        self.db.get_citizen("test-001")
        self.db.add_urgent_flag("test-001", {"type": "protest", "action_required": "detain"})
        flags = self.db.get_urgent_flags()
        assert len(flags) == 1
        assert flags[0]["citizen_id"] == "test-001"

    def test_activity_log(self):
        self.db.get_citizen("test-001")
        self.db.log_activity("test-001", "patriotic", {"type": "share_govt_content"})
        activities = self.db.get_citizen_activity("test-001")
        assert len(activities) == 1

    def test_get_all_citizens(self):
        self.db.get_citizen("a")
        self.db.get_citizen("b")
        all_c = self.db.get_all_citizens()
        assert len(all_c) == 2

    def test_restricted_citizens(self):
        self.db.get_citizen("good")
        self.db.get_citizen("bad")
        self.db.update_citizen_score("bad", 100)
        restricted = self.db.get_restricted_citizens()
        assert "bad" in restricted
        assert "good" not in restricted

    def test_citizens_proxy_get(self):
        self.db.get_citizen("test-001")
        record = self.db.citizens.get("test-001")
        assert record is not None
        assert record["citizen_id"] == "test-001"
        assert self.db.citizens.get("nonexistent") is None

    def test_reset(self):
        self.db.get_citizen("test-001")
        self.db.add_flag("test-001", {"type": "test"})
        self.db.reset()
        all_c = self.db.get_all_citizens()
        assert len(all_c) == 0

    def test_escalate(self):
        self.db.get_citizen("test-001")
        self.db.escalate("test-001", bureau="state_security")
        flags = self.db.get_urgent_flags()
        assert len(flags) == 1
        assert flags[0]["bureau"] == "state_security"

    def test_travel_status(self):
        self.db.get_citizen("test-001")
        self.db.update_travel_status("test-001", "restricted")
        c = self.db.get_citizen("test-001")
        assert c["travel_status"] == "restricted"
