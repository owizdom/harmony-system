"""SQLite-backed National Citizen Database.

Drop-in replacement for the old InMemoryDB — same interface, persistent storage.
"""

import datetime
import json
import os
import sqlite3

DB_PATH = os.environ.get("DB_PATH", "citizens.db")


def _now():
    return datetime.datetime.now().isoformat()


class SQLiteDB:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        c = self._conn
        c.execute("""
            CREATE TABLE IF NOT EXISTS citizens (
                citizen_id TEXT PRIMARY KEY,
                civic_score INTEGER DEFAULT 500,
                travel_status TEXT DEFAULT 'permitted',
                employment_clearance TEXT DEFAULT 'granted',
                service_access TEXT DEFAULT 'full',
                risk_tier TEXT DEFAULT 'normal',
                registered TEXT,
                flags_count INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                citizen_id TEXT NOT NULL,
                flag_data TEXT NOT NULL,
                created_at TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                citizen_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                added TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS urgent_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                flag_data TEXT NOT NULL,
                created_at TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                citizen_id TEXT NOT NULL,
                category TEXT NOT NULL,
                activity_data TEXT NOT NULL,
                created_at TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                citizen_a TEXT NOT NULL,
                citizen_b TEXT NOT NULL,
                edge_type TEXT NOT NULL DEFAULT 'weak_signal',
                weight REAL NOT NULL DEFAULT 0.5,
                created_at TEXT,
                UNIQUE(citizen_a, citizen_b)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS contagion_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seed_citizen_id TEXT NOT NULL,
                affected_count INTEGER,
                affected_fraction REAL,
                influence_radius INTEGER,
                result_data TEXT,
                created_at TEXT
            )
        """)
        # Indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_flags_citizen ON flags(citizen_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_citizen ON watchlist(citizen_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_activity_citizen ON activity_log(citizen_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_citizens_score ON citizens(civic_score)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_citizens_tier ON citizens(risk_tier)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rel_a ON relationships(citizen_a)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rel_b ON relationships(citizen_b)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_contagion_seed ON contagion_events(seed_citizen_id)")
        c.commit()

    # --- Citizen CRUD ---

    def get_citizen(self, citizen_id):
        row = self._conn.execute(
            "SELECT * FROM citizens WHERE citizen_id = ?", (citizen_id,)
        ).fetchone()
        if row is None:
            now = _now()
            self._conn.execute(
                "INSERT INTO citizens (citizen_id, registered) VALUES (?, ?)",
                (citizen_id, now),
            )
            self._conn.commit()
            return {
                "citizen_id": citizen_id,
                "civic_score": 500,
                "travel_status": "permitted",
                "employment_clearance": "granted",
                "service_access": "full",
                "risk_tier": "normal",
                "registered": now,
                "flags_count": 0,
            }
        return dict(row)

    def get_all_citizens(self):
        rows = self._conn.execute("SELECT * FROM citizens").fetchall()
        return {r["citizen_id"]: dict(r) for r in rows}

    def update_citizen_score(self, citizen_id, score):
        if score >= 700:
            risk_tier = "trusted"
        elif score >= 500:
            risk_tier = "normal"
        elif score >= 300:
            risk_tier = "suspicious"
        else:
            risk_tier = "dissident"

        fields = {"civic_score": score, "risk_tier": risk_tier}
        if risk_tier == "dissident":
            fields["travel_status"] = "banned"
            fields["employment_clearance"] = "revoked"
            fields["service_access"] = "restricted"

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [citizen_id]
        self._conn.execute(
            f"UPDATE citizens SET {set_clause} WHERE citizen_id = ?", values
        )
        self._conn.commit()

    # --- Flags ---

    def add_flag(self, citizen_id, flag):
        self._conn.execute(
            "INSERT INTO flags (citizen_id, flag_data, created_at) VALUES (?, ?, ?)",
            (citizen_id, json.dumps(flag), _now()),
        )
        count = self._conn.execute(
            "SELECT COUNT(*) FROM flags WHERE citizen_id = ?", (citizen_id,)
        ).fetchone()[0]
        self._conn.execute(
            "UPDATE citizens SET flags_count = ? WHERE citizen_id = ?",
            (count, citizen_id),
        )
        self._conn.commit()

    def get_citizen_flags(self, citizen_id):
        rows = self._conn.execute(
            "SELECT flag_data FROM flags WHERE citizen_id = ?", (citizen_id,)
        ).fetchall()
        return [json.loads(r["flag_data"]) for r in rows]

    # --- Urgent flags ---

    def add_urgent_flag(self, citizen_id, flag):
        data = {"citizen_id": citizen_id, "timestamp": _now(), **flag}
        self._conn.execute(
            "INSERT INTO urgent_flags (flag_data, created_at) VALUES (?, ?)",
            (json.dumps(data), _now()),
        )
        self._conn.commit()

    def get_urgent_flags(self):
        rows = self._conn.execute("SELECT flag_data FROM urgent_flags").fetchall()
        return [json.loads(r["flag_data"]) for r in rows]

    # --- Watchlist ---

    @property
    def watchlist(self):
        """Dict-like access for score_engine compatibility."""
        return self._get_watchlist_dict()

    def _get_watchlist_dict(self):
        rows = self._conn.execute("SELECT citizen_id, reason, added FROM watchlist").fetchall()
        result = {}
        for r in rows:
            result.setdefault(r["citizen_id"], []).append(
                {"reason": r["reason"], "added": r["added"]}
            )
        return result

    def add_to_watchlist(self, citizen_id, reason):
        self._conn.execute(
            "INSERT INTO watchlist (citizen_id, reason, added) VALUES (?, ?, ?)",
            (citizen_id, reason, _now()),
        )
        self._conn.commit()

    def get_watchlist(self):
        return self._get_watchlist_dict()

    # --- Travel ---

    def update_travel_status(self, citizen_id, status):
        self._conn.execute(
            "UPDATE citizens SET travel_status = ? WHERE citizen_id = ?",
            (status, citizen_id),
        )
        self._conn.commit()

    # --- Escalation ---

    def escalate(self, citizen_id, bureau):
        data = {
            "citizen_id": citizen_id,
            "bureau": bureau,
            "action": "investigate_and_detain",
            "timestamp": _now(),
        }
        self._conn.execute(
            "INSERT INTO urgent_flags (flag_data, created_at) VALUES (?, ?)",
            (json.dumps(data), _now()),
        )
        self._conn.commit()

    # --- Activity log ---

    def log_activity(self, citizen_id, category, activity):
        data = {"citizen_id": citizen_id, "category": category, "timestamp": _now(), **activity}
        self._conn.execute(
            "INSERT INTO activity_log (citizen_id, category, activity_data, created_at) VALUES (?, ?, ?, ?)",
            (citizen_id, category, json.dumps(data), _now()),
        )
        self._conn.commit()

    def get_citizen_activity(self, citizen_id):
        rows = self._conn.execute(
            "SELECT activity_data FROM activity_log WHERE citizen_id = ? ORDER BY created_at",
            (citizen_id,),
        ).fetchall()
        return [json.loads(r["activity_data"]) for r in rows]

    # --- Relationships ---

    def add_relationship(self, citizen_a, citizen_b, edge_type="weak_signal", weight=0.5):
        a, b = sorted([citizen_a, citizen_b])
        self._conn.execute(
            """INSERT OR REPLACE INTO relationships
               (citizen_a, citizen_b, edge_type, weight, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (a, b, edge_type, weight, _now()),
        )
        self._conn.commit()

    def add_relationships_bulk(self, relationships):
        for rel in relationships:
            a, b = sorted([rel["citizen_a"], rel["citizen_b"]])
            self._conn.execute(
                """INSERT OR REPLACE INTO relationships
                   (citizen_a, citizen_b, edge_type, weight, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (a, b, rel.get("edge_type", "weak_signal"),
                 rel.get("weight", 0.5), _now()),
            )
        self._conn.commit()

    def get_citizen_relationships(self, citizen_id):
        rows = self._conn.execute(
            """SELECT * FROM relationships
               WHERE citizen_a = ? OR citizen_b = ?""",
            (citizen_id, citizen_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_relationships(self):
        rows = self._conn.execute("SELECT * FROM relationships").fetchall()
        return [dict(r) for r in rows]

    def remove_relationship(self, citizen_a, citizen_b):
        a, b = sorted([citizen_a, citizen_b])
        self._conn.execute(
            "DELETE FROM relationships WHERE citizen_a = ? AND citizen_b = ?",
            (a, b),
        )
        self._conn.commit()

    # --- Contagion Events ---

    def log_contagion_event(self, seed_citizen_id, affected_count,
                            affected_fraction, influence_radius, result_data):
        self._conn.execute(
            """INSERT INTO contagion_events
               (seed_citizen_id, affected_count, affected_fraction,
                influence_radius, result_data, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (seed_citizen_id, affected_count, affected_fraction,
             influence_radius, json.dumps(result_data), _now()),
        )
        self._conn.commit()

    def get_contagion_events(self, citizen_id=None, limit=20):
        if citizen_id:
            rows = self._conn.execute(
                """SELECT * FROM contagion_events
                   WHERE seed_citizen_id = ? ORDER BY created_at DESC LIMIT ?""",
                (citizen_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM contagion_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["result_data"] = json.loads(d["result_data"]) if d["result_data"] else {}
            results.append(d)
        return results

    # --- Queries ---

    def get_restricted_citizens(self):
        rows = self._conn.execute(
            "SELECT * FROM citizens WHERE risk_tier = 'dissident'"
        ).fetchall()
        return {r["citizen_id"]: dict(r) for r in rows}

    # --- Admin ---

    @property
    def citizens(self):
        """Dict-like access for gateway compatibility."""
        return _CitizensProxy(self)

    def reset(self):
        """Drop all data."""
        for table in ("citizens", "flags", "watchlist", "urgent_flags",
                      "activity_log", "relationships", "contagion_events"):
            self._conn.execute(f"DELETE FROM {table}")
        self._conn.commit()

    def cleanup_activity_log(self, ttl_days: int = 90):
        """Delete activity log entries older than ttl_days."""
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=ttl_days)).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM activity_log WHERE created_at < ?", (cutoff,)
        )
        self._conn.commit()
        return cursor.rowcount

    def get_stats(self) -> dict:
        """Database statistics."""
        stats = {}
        for table in ("citizens", "flags", "watchlist", "urgent_flags",
                      "activity_log", "relationships", "contagion_events"):
            count = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            stats[table] = count
        return stats

    def close(self):
        self._conn.close()


class _CitizensProxy:
    """Provides dict-like .get() access for gateway.py compatibility."""

    def __init__(self, db):
        self._db = db

    def get(self, citizen_id, default=None):
        row = self._db._conn.execute(
            "SELECT * FROM citizens WHERE citizen_id = ?", (citizen_id,)
        ).fetchone()
        if row is None:
            return default
        return dict(row)

    def __contains__(self, citizen_id):
        return self.get(citizen_id) is not None

    def items(self):
        return self._db.get_all_citizens().items()


# Backward compatibility alias
InMemoryDB = SQLiteDB
