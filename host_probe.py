"""
Host Probe - Modulo di raccolta metriche hardware dell'host.

Legge CPU, RAM e I/O disco in modo non bloccante tramite psutil,
restituendo sempre un HostMetrics Pydantic validato.
In caso di errore, attiva graceful degradation (status: "degraded").
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import psutil

from contracts import HostMetrics

logger = logging.getLogger(__name__)

MAX_RETRIES: int = 3
_BACKOFF_BASE: float = 0.5


def _collect_cpu_percent(interval: float = 0.1) -> float:
    """Legge la percentuale CPU globale in modo sincrono (bloccante per `interval` sec).

    Args:
        interval: Secondi di campionamento per psutil.cpu_percent.

    Returns:
        Percentuale CPU (0.0-100.0).

    Raises:
        OSError: Se /proc/stat o equivalente non è accessibile.
    """
    return psutil.cpu_percent(interval=interval)


def _collect_ram_percent() -> float:
    """Legge la percentuale RAM utilizzata.

    Returns:
        Percentuale RAM (0.0-100.0).

    Raises:
        OSError: Se le info di memoria non sono accessibili.
    """
    return psutil.virtual_memory().percent


def _collect_disk_io() -> tuple[int, int]:
    """Legge i contatori cumulativi di I/O disco globale.

    Returns:
        Tupla (read_bytes, write_bytes) cumulativi.

    Raises:
        OSError: Se i contatori disco non sono disponibili.
    """
    counters = psutil.disk_io_counters()
    if counters is None:
        return (0, 0)
    return (counters.read_bytes, counters.write_bytes)


def _collect_net_io() -> tuple[int, int]:
    """Legge i contatori cumulativi di Network I/O globale.

    Returns:
        Tupla (sent_bytes, recv_bytes) cumulativi.
    """
    counters = psutil.net_io_counters()
    if counters is None:
        return (0, 0)
    return (counters.bytes_sent, counters.bytes_recv)


def _collect_disk_usage(path: str = "/") -> tuple[float, float, float, float]:
    """Legge lo spazio disco per il path specificato.

    Returns:
        Tupla (total_gb, used_gb, free_gb, percent).
    """
    usage = psutil.disk_usage(path)
    gb = 1024**3
    return (
        round(usage.total / gb, 2),
        round(usage.used / gb, 2),
        round(usage.free / gb, 2),
        usage.percent,
    )


def collect_host_metrics_sync() -> HostMetrics:
    """Raccoglie tutte le metriche host in modo sincrono con retry e fallback.

    Tenta fino a MAX_RETRIES volte. Se tutti i tentativi falliscono,
    restituisce metriche di default con status 'degraded'.

    Returns:
        HostMetrics validato tramite Pydantic.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            cpu: float = _collect_cpu_percent()
            ram: float = _collect_ram_percent()
            read_bytes, write_bytes = _collect_disk_io()
            net_sent, net_recv = _collect_net_io()
            d_total, d_used, d_free, d_pct = _collect_disk_usage()

            return HostMetrics(
                timestamp=datetime.now(timezone.utc),
                cpu_percent=cpu,
                ram_percent=ram,
                disk_io_read_bytes=read_bytes,
                disk_io_write_bytes=write_bytes,
                net_io_sent_bytes=net_sent,
                net_io_recv_bytes=net_recv,
                disk_total_gb=d_total,
                disk_used_gb=d_used,
                disk_free_gb=d_free,
                disk_percent=d_pct,
                status="ok",
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "host_probe: tentativo %d/%d fallito: %s",
                attempt,
                MAX_RETRIES,
                exc,
            )

    logger.error(
        "host_probe: tutti i %d tentativi falliti. Attivazione fallback degraded. "
        "Ultimo errore: %s",
        MAX_RETRIES,
        last_error,
    )
    return HostMetrics(
        timestamp=datetime.now(timezone.utc),
        cpu_percent=0.0,
        ram_percent=0.0,
        disk_io_read_bytes=0,
        disk_io_write_bytes=0,
        status="degraded",
    )


async def collect_host_metrics() -> HostMetrics:
    """Wrapper asincrono non bloccante per la raccolta metriche host.

    Esegue la lettura sincrona di psutil in un thread separato
    tramite asyncio.to_thread() per non bloccare l'event loop.

    Returns:
        HostMetrics validato tramite Pydantic.
    """
    return await asyncio.to_thread(collect_host_metrics_sync)
