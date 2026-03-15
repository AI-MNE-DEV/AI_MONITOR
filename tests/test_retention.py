"""
Test suite per il modulo retention.py.

Testa la pulizia periodica dei record vecchi dal database SQLite.
"""

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

from retention import purge_old_records


def _create_test_db() -> str:
    """Crea un DB di test con record vecchi e recenti."""
    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = db_file.name
    db_file.close()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute(
        """CREATE TABLE host_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            cpu_percent REAL NOT NULL,
            ram_percent REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'ok'
        )"""
    )
    conn.execute(
        """CREATE TABLE docker_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            total_containers INTEGER NOT NULL,
            running_containers INTEGER NOT NULL,
            containers_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'ok'
        )"""
    )
    conn.execute(
        """CREATE TABLE alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            level TEXT NOT NULL,
            source TEXT NOT NULL,
            message TEXT NOT NULL,
            metric_value REAL
        )"""
    )

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)
    recent = now - timedelta(hours=1)

    # 5 record vecchi + 3 recenti per host_metrics
    for i in range(5):
        ts = (old + timedelta(hours=i)).isoformat()
        conn.execute(
            "INSERT INTO host_metrics (timestamp, cpu_percent, ram_percent) VALUES (?, ?, ?)",
            (ts, 50.0, 60.0),
        )
    for i in range(3):
        ts = (recent + timedelta(minutes=i)).isoformat()
        conn.execute(
            "INSERT INTO host_metrics (timestamp, cpu_percent, ram_percent) VALUES (?, ?, ?)",
            (ts, 50.0, 60.0),
        )

    # 4 record vecchi + 2 recenti per docker_metrics
    for i in range(4):
        ts = (old + timedelta(hours=i)).isoformat()
        conn.execute(
            "INSERT INTO docker_metrics (timestamp, total_containers, running_containers) VALUES (?, ?, ?)",
            (ts, 3, 2),
        )
    for i in range(2):
        ts = (recent + timedelta(minutes=i)).isoformat()
        conn.execute(
            "INSERT INTO docker_metrics (timestamp, total_containers, running_containers) VALUES (?, ?, ?)",
            (ts, 3, 2),
        )

    # 2 record vecchi + 1 recente per alert_events
    for i in range(2):
        ts = (old + timedelta(hours=i)).isoformat()
        conn.execute(
            "INSERT INTO alert_events (alert_id, timestamp, level, source, message) VALUES (?, ?, ?, ?, ?)",
            (f"old_{i}", ts, "CRITICAL", "host", "test alert"),
        )
    conn.execute(
        "INSERT INTO alert_events (alert_id, timestamp, level, source, message) VALUES (?, ?, ?, ?, ?)",
        ("new_0", recent.isoformat(), "WARNING", "host", "test alert"),
    )

    conn.commit()
    conn.close()
    return db_path


class TestPurgeOldRecords:
    def test_deletes_old_records(self):
        db_path = _create_test_db()
        deleted = purge_old_records(db_path, retention_days=7)
        assert deleted == 11  # 5 host + 4 docker + 2 alerts

    def test_keeps_recent_records(self):
        db_path = _create_test_db()
        purge_old_records(db_path, retention_days=7)

        conn = sqlite3.connect(db_path)
        host_count = conn.execute("SELECT COUNT(*) FROM host_metrics").fetchone()[0]
        docker_count = conn.execute("SELECT COUNT(*) FROM docker_metrics").fetchone()[0]
        alert_count = conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
        conn.close()

        assert host_count == 3
        assert docker_count == 2
        assert alert_count == 1

    def test_zero_retention_deletes_all(self):
        db_path = _create_test_db()
        deleted = purge_old_records(db_path, retention_days=0)
        # 8 host + 6 docker + 3 alerts = 17 record totali, tutti nel passato
        assert deleted == 17

    def test_large_retention_deletes_nothing(self):
        db_path = _create_test_db()
        deleted = purge_old_records(db_path, retention_days=365)
        assert deleted == 0

    def test_handles_missing_db(self):
        deleted = purge_old_records("/nonexistent/path.db", retention_days=7)
        assert deleted == 0

    def test_idempotent_double_purge(self):
        db_path = _create_test_db()
        first = purge_old_records(db_path, retention_days=7)
        second = purge_old_records(db_path, retention_days=7)
        assert first == 11
        assert second == 0
