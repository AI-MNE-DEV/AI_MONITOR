"""
Data Retention - Job di pulizia periodica del database.

Elimina le metriche più vecchie di N giorni (configurabile via env)
per prevenire la saturazione del disco. Eseguito come task asyncio
in background, non blocca mai la pipeline di raccolta dati.
"""

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Configurazione via env
RETENTION_DAYS: int = int(os.getenv("AI_MONITOR_RETENTION_DAYS", "7"))
RETENTION_INTERVAL_HOURS: float = float(
    os.getenv("AI_MONITOR_RETENTION_INTERVAL_HOURS", "6")
)

_TABLES: list[str] = ["host_metrics", "docker_metrics", "alert_events"]


def purge_old_records(db_path: str, retention_days: int = RETENTION_DAYS) -> int:
    """Elimina i record più vecchi di retention_days da tutte le tabelle metriche.

    Args:
        db_path: Percorso del file SQLite.
        retention_days: Giorni di retention. Record più vecchi vengono eliminati.

    Returns:
        Numero totale di record eliminati.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    total_deleted = 0

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout=5000")

        for table in _TABLES:
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE timestamp < ?",  # noqa: S608
                (cutoff,),
            )
            deleted = cursor.rowcount
            total_deleted += deleted
            if deleted > 0:
                logger.info(
                    "retention: eliminati %d record da %s (cutoff: %s)",
                    deleted,
                    table,
                    cutoff[:19],
                )

        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("retention: errore durante purge: %s", exc)

    return total_deleted


async def retention_loop(db_path: str) -> None:
    """Loop asincrono di retention che esegue purge periodicamente.

    Args:
        db_path: Percorso del file SQLite.
    """
    interval_seconds = RETENTION_INTERVAL_HOURS * 3600
    logger.info(
        "retention: avviato (retention=%d giorni, intervallo=%.1fh)",
        RETENTION_DAYS,
        RETENTION_INTERVAL_HOURS,
    )

    while True:
        try:
            deleted = await asyncio.to_thread(purge_old_records, db_path)
            if deleted > 0:
                logger.info("retention: ciclo completato, %d record eliminati", deleted)
        except Exception as exc:
            logger.warning("retention: errore nel loop: %s", exc)

        await asyncio.sleep(interval_seconds)
