"""Test per storage_engine.py.

Verifica schema DB, scrittura bufferizzata, batch insert,
graceful shutdown e throughput con 1000 record.
"""

import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone

from contracts import AlertEvent, DockerMetrics, HostMetrics
from storage_engine import StorageEngine, _init_db


def _make_host_metrics(cpu: float = 25.0, ram: float = 50.0) -> HostMetrics:
    """Helper per creare HostMetrics di test."""
    return HostMetrics(
        timestamp=datetime.now(timezone.utc),
        cpu_percent=cpu,
        ram_percent=ram,
        disk_io_read_bytes=1000,
        disk_io_write_bytes=2000,
        status="ok",
    )


def _make_docker_metrics() -> DockerMetrics:
    """Helper per creare DockerMetrics di test."""
    return DockerMetrics(
        timestamp=datetime.now(timezone.utc),
        total_containers=3,
        running_containers=2,
        containers=[],
        status="ok",
    )


def _make_alert_event() -> AlertEvent:
    """Helper per creare AlertEvent di test."""
    return AlertEvent(
        id="alert-001",
        timestamp=datetime.now(timezone.utc),
        level="WARNING",
        source="host",
        message="CPU sopra il 90%",
        metric_value=92.5,
    )


class TestInitDb:
    """Test per l'inizializzazione dello schema DB."""

    def test_creates_tables(self) -> None:
        conn = sqlite3.connect(":memory:")
        _init_db(conn)

        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]

        assert "host_metrics" in tables
        assert "docker_metrics" in tables
        assert "alert_events" in tables
        conn.close()

    def test_wal_mode_enabled(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            conn = sqlite3.connect(db_path)
            _init_db(conn)
            result = conn.execute("PRAGMA journal_mode").fetchone()
            assert result[0] == "wal"
            conn.close()
        finally:
            for ext in ("", "-wal", "-shm"):
                path = db_path + ext
                if os.path.exists(path):
                    os.unlink(path)

    def test_idempotent_init(self) -> None:
        conn = sqlite3.connect(":memory:")
        _init_db(conn)
        _init_db(conn)  # seconda volta, non deve crashare
        cursor = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
        assert cursor.fetchone()[0] >= 3
        conn.close()


class TestStorageEngine:
    """Test per il ciclo di vita e la scrittura del StorageEngine."""

    def _make_engine(self, tmp_dir: str) -> StorageEngine:
        """Crea un engine con DB in directory temporanea."""
        db_path = os.path.join(tmp_dir, "test.db")
        return StorageEngine(db_path=db_path, batch_size=10)

    def test_start_and_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._make_engine(tmp)
            engine.start()
            assert engine._running is True
            engine.stop()
            assert engine._running is False

    def test_store_host_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            engine = StorageEngine(db_path=db_path, batch_size=5)
            engine.start()

            for _ in range(5):
                engine.store(_make_host_metrics())

            time.sleep(1.0)
            engine.stop()

            conn = sqlite3.connect(db_path)
            count = conn.execute("SELECT COUNT(*) FROM host_metrics").fetchone()[0]
            assert count == 5
            conn.close()

    def test_store_docker_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            engine = StorageEngine(db_path=db_path, batch_size=5)
            engine.start()

            engine.store(_make_docker_metrics())
            time.sleep(1.0)
            engine.stop()

            conn = sqlite3.connect(db_path)
            count = conn.execute("SELECT COUNT(*) FROM docker_metrics").fetchone()[0]
            assert count == 1
            conn.close()

    def test_store_alert_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            engine = StorageEngine(db_path=db_path, batch_size=5)
            engine.start()

            engine.store(_make_alert_event())
            time.sleep(1.0)
            engine.stop()

            conn = sqlite3.connect(db_path)
            count = conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
            assert count == 1

            row = conn.execute(
                "SELECT alert_id, level, source FROM alert_events"
            ).fetchone()
            assert row[0] == "alert-001"
            assert row[1] == "WARNING"
            assert row[2] == "host"
            conn.close()

    def test_mixed_record_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            engine = StorageEngine(db_path=db_path, batch_size=10)
            engine.start()

            engine.store(_make_host_metrics())
            engine.store(_make_docker_metrics())
            engine.store(_make_alert_event())

            time.sleep(1.0)
            engine.stop()

            conn = sqlite3.connect(db_path)
            h = conn.execute("SELECT COUNT(*) FROM host_metrics").fetchone()[0]
            d = conn.execute("SELECT COUNT(*) FROM docker_metrics").fetchone()[0]
            a = conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
            assert h == 1
            assert d == 1
            assert a == 1
            conn.close()

    def test_records_written_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            engine = StorageEngine(db_path=db_path, batch_size=5)
            engine.start()

            for _ in range(7):
                engine.store(_make_host_metrics())

            time.sleep(1.0)
            engine.stop()

            assert engine.records_written == 7

    def test_graceful_shutdown_flushes_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            engine = StorageEngine(db_path=db_path, batch_size=100)
            engine.start()

            for _ in range(20):
                engine.store(_make_host_metrics())

            engine.stop(timeout=5.0)

            conn = sqlite3.connect(db_path)
            count = conn.execute("SELECT COUNT(*) FROM host_metrics").fetchone()[0]
            assert count == 20
            conn.close()


class TestStorageEngineThroughput:
    """Integration test: throughput con 1000 record."""

    def test_1000_records_throughput(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "bench.db")
            engine = StorageEngine(db_path=db_path, batch_size=50)
            engine.start()

            start_time = time.monotonic()
            for i in range(1000):
                engine.store(_make_host_metrics(cpu=float(i % 100), ram=float(i % 100)))
            enqueue_time = time.monotonic() - start_time

            engine.stop(timeout=30.0)

            conn = sqlite3.connect(db_path)
            count = conn.execute("SELECT COUNT(*) FROM host_metrics").fetchone()[0]
            conn.close()

            assert count == 1000
            assert engine.records_written == 1000
            # L'enqueue deve essere quasi istantaneo (non bloccante)
            assert enqueue_time < 1.0, f"Enqueue troppo lento: {enqueue_time:.2f}s"
