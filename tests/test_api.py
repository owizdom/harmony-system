"""Tests for Flask API endpoints."""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["DB_PATH"] = ":memory:"

from app import app


class TestAPI:
    def setup_method(self):
        self.client = app.test_client()
        self.client.post("/reset")

    def test_classify(self):
        r = self.client.post("/classify", json={"text": "The corrupt regime must fall"})
        assert r.status_code == 200
        data = r.json
        assert data["stance"] == "critical"
        assert data["score_adjustment"] < 0

    def test_classify_empty(self):
        r = self.client.post("/classify", json={"text": ""})
        assert r.status_code == 400

    def test_classify_batch(self):
        r = self.client.post("/classify/batch", json={
            "texts": ["Our great leader", "Corrupt dictator"],
            "platform": "test",
        })
        assert r.status_code == 200
        assert r.json["count"] == 2

    def test_classify_batch_limit(self):
        r = self.client.post("/classify/batch", json={"texts": ["x"] * 51})
        assert r.status_code == 400

    def test_classify_stats(self):
        self.client.post("/classify", json={"text": "test"})
        r = self.client.get("/classify/stats")
        assert r.status_code == 200
        assert r.json["total"] >= 1

    def test_citizen_score(self):
        r = self.client.get("/citizen/test-001/score")
        assert r.status_code == 200
        assert r.json["civic_score"] == 500

    def test_citizen_profile(self):
        r = self.client.get("/citizen/test-001/profile")
        assert r.status_code == 200
        assert "tier" in r.json
        assert "privileges" in r.json

    def test_ingest_activity(self):
        r = self.client.post("/citizen/test-001/ingest", json={
            "activities": [{"type": "post_criticism", "content": "Government is corrupt", "platform": "twitter"}]
        })
        assert r.status_code == 200
        assert r.json["updated_score"] < 500

    def test_demo(self):
        r = self.client.post("/demo/run")
        assert r.status_code == 200
        r = self.client.get("/citizens")
        assert len(r.json) == 3

    def test_watchlist(self):
        self.client.post("/demo/run")
        r = self.client.get("/watchlist")
        assert r.status_code == 200

    def test_restricted(self):
        self.client.post("/demo/run")
        r = self.client.get("/restricted")
        assert r.status_code == 200

    def test_urgent(self):
        self.client.post("/demo/run")
        r = self.client.get("/urgent")
        assert r.status_code == 200
        assert len(r.json) > 0

    def test_reset(self):
        self.client.post("/demo/run")
        self.client.post("/reset")
        r = self.client.get("/citizens")
        assert len(r.json) == 0

    def test_dashboard(self):
        r = self.client.get("/dashboard")
        assert r.status_code == 200
        assert b"NLP Classification Pipeline" in r.data

    def test_gateway_no_key(self):
        r = self.client.post("/gateway/check", json={"citizen_id": "x", "service": "travel"})
        assert r.status_code == 401

    def test_gateway_check(self):
        self.client.post("/demo/run")
        r = self.client.post("/gateway/check",
            json={"citizen_id": "citizen-B", "service": "travel"},
            headers={"X-API-Key": "gw_demo_key"})
        assert r.status_code == 200
        assert r.json["allowed"] is False
        assert r.json["tier"] == "DISSIDENT"

    def test_gateway_register(self):
        r = self.client.post("/gateway/register", json={"system_name": "test_system"})
        assert r.status_code == 201
        key = r.json["api_key"]
        # Use the new key
        self.client.post("/demo/run")
        r = self.client.get("/gateway/citizen/citizen-A", headers={"X-API-Key": key})
        assert r.status_code == 200

    def test_bulk_import_json(self):
        r = self.client.post("/import", json={"posts": [
            {"citizen_id": "imp-001", "content": "Our great leader is visionary", "platform": "test"},
            {"citizen_id": "imp-002", "content": "Corrupt tyranny", "platform": "test"},
        ]})
        assert r.status_code == 200
        assert r.json["summary"]["processed"] == 2

    def test_bulk_import_csv(self):
        csv = "citizen_id,content,platform\ncsv-001,Long live our leader,weibo\ncsv-002,Resign dictator,twitter\n"
        r = self.client.post("/import",
            data={"file": (io.BytesIO(csv.encode()), "test.csv")},
            content_type="multipart/form-data")
        assert r.status_code == 200
        assert r.json["summary"]["processed"] == 2

    def test_bulk_import_empty(self):
        r = self.client.post("/import", json={"posts": []})
        assert r.status_code == 400

    def test_ingestion_stats(self):
        r = self.client.get("/ingestion/stats")
        assert r.status_code == 200

    def test_ingestion_drain_empty(self):
        r = self.client.post("/ingestion/drain")
        assert r.status_code == 200
        assert r.json["processed"] == 0
