"""
Storage Engine - Modulo di persistenza metriche su SQLite in WAL mode.

Implementa un writer thread dedicato con coda bufferizzata per disaccoppiare
i produttori (probe) dal consumatore (DB writer). Le scritture avvengono
in batch per massimizzare il throughput e minimizzare le transazioni.
"""

import logging
import os
import queue
import sqlite3
import threading
from typing import Optional, Union

from contracts import AlertEvent, DockerMetrics, HostMetrics

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH: str = os.getenv("AI_MONITOR_DB_PATH", "data/ai_monitor.db")
_BATCH_SIZE: int = 50
_QUEUE_TIMEOUT: float = 1.0
_MAX_RETRIES: int = 3

# Tipo sentinella per segnalare lo shutdown del writer
_SHUTDOWN = object()

# Tipo union per i record nella coda
QueueItem = Union[HostMetrics, DockerMetrics, AlertEvent, object]


def _init_db(conn: sqlite3.Connection) -> None:
    """Crea le tabelle se non esistono e abilita WAL mode.

    Args:
        conn: Connessione SQLite attiva.
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS host_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            cpu_percent REAL NOT NULL,
            ram_percent REAL NOT NULL,
            disk_io_read_bytes INTEGER NOT NULL DEFAULT 0,
            disk_io_write_bytes INTEGER NOT NULL DEFAULT 0,
            net_io_sent_bytes INTEGER NOT NULL DEFAULT 0,
            net_io_recv_bytes INTEGER NOT NULL DEFAULT 0,
            disk_total_gb REAL NOT NULL DEFAULT 0,
            disk_used_gb REAL NOT NULL DEFAULT 0,
            disk_free_gb REAL NOT NULL DEFAULT 0,
            disk_percent REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ok'
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS docker_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            total_containers INTEGER NOT NULL,
            running_containers INTEGER NOT NULL,
            containers_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'ok'
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            level TEXT NOT NULL,
            source TEXT NOT NULL,
            message TEXT NOT NULL,
            metric_value REAL
        )
        """
    )

    conn.commit()

    # Migrazioni: aggiunge colonne mancanti a tabelle pre-esistenti
    _migrate_add_columns(conn)

    logger.info("storage_engine: schema DB inizializzato (WAL mode)")


def _migrate_add_columns(conn: sqlite3.Connection) -> None:
    """Aggiunge colonne introdotte nello Sprint 6 a tabelle pre-esistenti."""
    migrations = [
        ("host_metrics", "net_io_sent_bytes", "INTEGER NOT NULL DEFAULT 0"),
        ("host_metrics", "net_io_recv_bytes", "INTEGER NOT NULL DEFAULT 0"),
        ("host_metrics", "disk_total_gb", "REAL NOT NULL DEFAULT 0"),
        ("host_metrics", "disk_used_gb", "REAL NOT NULL DEFAULT 0"),
        ("host_metrics", "disk_free_gb", "REAL NOT NULL DEFAULT 0"),
        ("host_metrics", "disk_percent", "REAL NOT NULL DEFAULT 0"),
    ]
    for table, column, col_type in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            logger.info("storage_engine: migrazione - aggiunta %s.%s", table, column)
        except sqlite3.OperationalError:
            pass  # colonna già presente
    conn.commit()


def _insert_host_metrics(conn: sqlite3.Connection, metrics: HostMetrics) -> None:
    """Inserisce un record HostMetrics nel DB.

    Args:
        conn: Connessione SQLite attiva.
        metrics: Metriche host validate da Pydantic.
    """
    conn.execute(
        """
        INSERT INTO host_metrics
            (timestamp, cpu_percent, ram_percent, disk_io_read_bytes,
             disk_io_write_bytes, net_io_sent_bytes, net_io_recv_bytes,
             disk_total_gb, disk_used_gb, disk_free_gb, disk_percent, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metrics.timestamp.isoformat(),
            metrics.cpu_percent,
            metrics.ram_percent,
            metrics.disk_io_read_bytes,
            metrics.disk_io_write_bytes,
            metrics.net_io_sent_bytes,
            metrics.net_io_recv_bytes,
            metrics.disk_total_gb,
            metrics.disk_used_gb,
            metrics.disk_free_gb,
            metrics.disk_percent,
            metrics.status,
        ),
    )


def _insert_docker_metrics(conn: sqlite3.Connection, metrics: DockerMetrics) -> None:
    """Inserisce un record DockerMetrics nel DB.

    Args:
        conn: Connessione SQLite attiva.
        metrics: Metriche Docker validate da Pydantic.
    """
    containers_json = (
        "[" + ",".join(c.model_dump_json() for c in metrics.containers) + "]"
    )
    conn.execute(
        """
        INSERT INTO docker_metrics
            (timestamp, total_containers, running_containers,
             containers_json, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            metrics.timestamp.isoformat(),
            metrics.total_containers,
            metrics.running_containers,
            containers_json,
            metrics.status,
        ),
    )


def _insert_alert_event(conn: sqlite3.Connection, alert: AlertEvent) -> None:
    """Inserisce un record AlertEvent nel DB.

    Args:
        conn: Connessione SQLite attiva.
        alert: Evento allarme validato da Pydantic.
    """
    conn.execute(
        """
        INSERT INTO alert_events
            (alert_id, timestamp, level, source, message, metric_value)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            alert.id,
            alert.timestamp.isoformat(),
            alert.level,
            alert.source,
            alert.message,
            alert.metric_value,
        ),
    )


class StorageEngine:
    """Engine di persistenza con writer thread dedicato e coda bufferizzata.

    Il pattern produttore/consumatore disaccoppia i probe dal DB:
    i probe chiamano `store()` che è non bloccante (enqueue),
    il writer thread consuma la coda e scrive in batch.

    Args:
        db_path: Percorso del file SQLite. Default da env AI_MONITOR_DB_PATH.
        batch_size: Numero di record da raggruppare in una singola transazione.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        batch_size: int = _BATCH_SIZE,
    ) -> None:
        self._db_path: str = db_path or _DEFAULT_DB_PATH
        self._batch_size: int = batch_size
        self._queue: queue.Queue[QueueItem] = queue.Queue()
        self._writer_thread: Optional[threading.Thread] = None
        self._running: bool = False
        self._conn: Optional[sqlite3.Connection] = None
        self._records_written: int = 0

    @property
    def records_written(self) -> int:
        """Numero totale di record scritti dal boot."""
        return self._records_written

    @property
    def queue_size(self) -> int:
        """Numero di record in attesa nella coda."""
        return self._queue.qsize()

    def start(self) -> None:
        """Avvia il writer thread e inizializza il DB.

        Raises:
            RuntimeError: Se il motore è già avviato.
        """
        if self._running:
            raise RuntimeError("StorageEngine è già avviato")

        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)

        self._running = True
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="storage-writer",
            daemon=True,
        )
        self._writer_thread.start()
        logger.info("storage_engine: writer thread avviato (db=%s)", self._db_path)

    def stop(self, timeout: float = 5.0) -> None:
        """Ferma il writer thread in modo graceful, svuotando la coda residua.

        Args:
            timeout: Secondi massimi di attesa per il join del thread.
        """
        if not self._running:
            return

        self._running = False
        self._queue.put(_SHUTDOWN)

        if self._writer_thread and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=timeout)

        logger.info(
            "storage_engine: writer thread fermato. Record totali scritti: %d",
            self._records_written,
        )

    def store(self, record: Union[HostMetrics, DockerMetrics, AlertEvent]) -> None:
        """Accoda un record per la scrittura asincrona (non bloccante).

        Args:
            record: Modello Pydantic validato da persistere.
        """
        self._queue.put(record)

    def _writer_loop(self) -> None:
        """Loop principale del writer thread. Consuma la coda e scrive in batch."""
        try:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            _init_db(self._conn)
        except Exception as exc:
            logger.error("storage_engine: impossibile aprire il DB: %s", exc)
            self._running = False
            return

        while self._running or not self._queue.empty():
            batch: list[Union[HostMetrics, DockerMetrics, AlertEvent]] = []

            try:
                item = self._queue.get(timeout=_QUEUE_TIMEOUT)
                if item is _SHUTDOWN:
                    self._flush_remaining(batch)
                    break
                batch.append(item)  # type: ignore[arg-type]
            except queue.Empty:
                continue

            # Drain fino a batch_size
            while len(batch) < self._batch_size:
                try:
                    item = self._queue.get_nowait()
                    if item is _SHUTDOWN:
                        self._write_batch(batch)
                        self._flush_remaining(batch=[])
                        self._close_conn()
                        return
                    batch.append(item)  # type: ignore[arg-type]
                except queue.Empty:
                    break

            self._write_batch(batch)

        self._close_conn()

    def _write_batch(
        self, batch: list[Union[HostMetrics, DockerMetrics, AlertEvent]]
    ) -> None:
        """Scrive un batch di record nel DB con retry.

        Args:
            batch: Lista di record Pydantic da persistere.
        """
        if not batch or not self._conn:
            return

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                for record in batch:
                    if isinstance(record, HostMetrics):
                        _insert_host_metrics(self._conn, record)
                    elif isinstance(record, DockerMetrics):
                        _insert_docker_metrics(self._conn, record)
                    elif isinstance(record, AlertEvent):
                        _insert_alert_event(self._conn, record)

                self._conn.commit()
                self._records_written += len(batch)
                return

            except sqlite3.OperationalError as exc:
                logger.warning(
                    "storage_engine: batch write tentativo %d/%d fallito: %s",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                )
                if attempt == _MAX_RETRIES:
                    logger.error(
                        "storage_engine: batch di %d record perso dopo %d tentativi. "
                        "Errore: %s",
                        len(batch),
                        _MAX_RETRIES,
                        exc,
                    )

    def _flush_remaining(
        self, batch: list[Union[HostMetrics, DockerMetrics, AlertEvent]]
    ) -> None:
        """Svuota la coda residua e scrive tutto prima dello shutdown.

        Args:
            batch: Batch corrente parzialmente riempito.
        """
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                if item is not _SHUTDOWN:
                    batch.append(item)  # type: ignore[arg-type]
            except queue.Empty:
                break

        if batch:
            self._write_batch(batch)

    def _close_conn(self) -> None:
        """Chiude la connessione al DB in modo sicuro."""
        if self._conn:
            try:
                self._conn.close()
            except Exception as exc:
                logger.warning("storage_engine: errore chiusura DB: %s", exc)
            self._conn = None
