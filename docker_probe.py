"""
Docker Probe - Modulo di raccolta metriche e eventi dei container Docker.

Interroga il Docker Engine via SDK Python per ottenere stato, CPU e RAM
di ogni container. Ascolta l'event stream per rilevare start/stop/die
in tempo reale. Implementa graceful degradation con backoff esponenziale
se il socket Docker non è raggiungibile.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import docker
from docker.errors import DockerException

from contracts import ContainerMetrics, DockerMetrics

logger = logging.getLogger(__name__)

MAX_RETRIES: int = 3
_BACKOFF_BASE: float = 1.0
_BACKOFF_MAX: float = 8.0


def _create_client() -> docker.DockerClient:
    """Crea un client Docker dal socket di default o da DOCKER_HOST env.

    Returns:
        Istanza DockerClient connessa.

    Raises:
        DockerException: Se il socket non è raggiungibile.
    """
    return docker.DockerClient.from_env()


def _parse_cpu_percent(stats: dict[str, Any]) -> float:
    """Calcola la percentuale CPU di un container dai suoi stats JSON.

    Usa la formula ufficiale Docker:
    delta_cpu / delta_system * num_cpus * 100

    Args:
        stats: Dizionario stats restituito da container.stats(stream=False).

    Returns:
        Percentuale CPU (0.0-100.0+), 0.0 se dati insufficienti.
    """
    cpu_stats = stats.get("cpu_stats", {})
    precpu_stats = stats.get("precpu_stats", {})

    cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - precpu_stats.get(
        "cpu_usage", {}
    ).get("total_usage", 0)

    system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get(
        "system_cpu_usage", 0
    )

    num_cpus = cpu_stats.get("online_cpus") or len(
        cpu_stats.get("cpu_usage", {}).get("percpu_usage", []) or [1]
    )

    if system_delta > 0 and cpu_delta >= 0:
        return round((cpu_delta / system_delta) * num_cpus * 100.0, 2)
    return 0.0


def _parse_ram_usage_mb(stats: dict[str, Any]) -> float:
    """Estrae l'utilizzo RAM in MB dai container stats.

    Args:
        stats: Dizionario stats restituito da container.stats(stream=False).

    Returns:
        RAM in MB, 0.0 se non disponibile.
    """
    memory_stats = stats.get("memory_stats", {})
    usage_bytes = memory_stats.get("usage", 0)
    cache_bytes = memory_stats.get("stats", {}).get("cache", 0)
    return round((usage_bytes - cache_bytes) / (1024 * 1024), 2)


def _parse_net_io(stats: dict[str, Any]) -> tuple[int, int]:
    """Estrae Network I/O (TX, RX) dai container stats.

    Returns:
        Tupla (sent_bytes, recv_bytes).
    """
    networks = stats.get("networks", {})
    tx_bytes = sum(n.get("tx_bytes", 0) for n in networks.values())
    rx_bytes = sum(n.get("rx_bytes", 0) for n in networks.values())
    return (tx_bytes, rx_bytes)


def _parse_block_io(stats: dict[str, Any]) -> tuple[int, int]:
    """Estrae Block I/O (read, write) dai container stats.

    Returns:
        Tupla (read_bytes, write_bytes).
    """
    blkio = stats.get("blkio_stats", {})
    entries = blkio.get("io_service_bytes_recursive") or []
    read_bytes = sum(e.get("value", 0) for e in entries if e.get("op") == "read")
    write_bytes = sum(e.get("value", 0) for e in entries if e.get("op") == "write")
    return (read_bytes, write_bytes)


def _get_uptime_seconds(container: Any) -> Optional[int]:
    """Calcola i secondi di uptime di un container dal suo started_at.

    Args:
        container: Oggetto container Docker SDK.

    Returns:
        Secondi di uptime o None se non running.
    """
    if container.status != "running":
        return None
    try:
        started_at = container.attrs.get("State", {}).get("StartedAt", "")
        if started_at and started_at != "0001-01-01T00:00:00Z":
            start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - start_dt
            return max(0, int(delta.total_seconds()))
    except (ValueError, TypeError) as exc:
        logger.debug("Impossibile calcolare uptime per %s: %s", container.name, exc)
    return None


def collect_docker_metrics_sync() -> DockerMetrics:
    """Raccoglie metriche di tutti i container con retry e fallback degraded.

    Tenta fino a MAX_RETRIES volte la connessione al Docker Engine.
    Per ogni container running, legge gli stats (CPU/RAM).
    Se tutti i tentativi falliscono, restituisce DockerMetrics(status="degraded").

    Returns:
        DockerMetrics validato tramite Pydantic.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client = _create_client()
            all_containers = client.containers.list(all=True)

            container_metrics: list[ContainerMetrics] = []
            running_count = 0

            for container in all_containers:
                c_status = container.status
                if c_status == "running":
                    running_count += 1

                cpu_pct = 0.0
                ram_mb = 0.0

                net_tx = 0
                net_rx = 0
                blk_read = 0
                blk_write = 0

                if c_status == "running":
                    try:
                        stats = container.stats(stream=False)
                        cpu_pct = _parse_cpu_percent(stats)
                        ram_mb = _parse_ram_usage_mb(stats)
                        net_tx, net_rx = _parse_net_io(stats)
                        blk_read, blk_write = _parse_block_io(stats)
                    except Exception as stats_exc:
                        logger.warning(
                            "docker_probe: stats falliti per %s: %s",
                            container.name,
                            stats_exc,
                        )

                container_metrics.append(
                    ContainerMetrics(
                        container_id=container.short_id,
                        name=container.name,
                        status=c_status,
                        cpu_percent=cpu_pct,
                        ram_usage_mb=ram_mb,
                        net_io_sent_bytes=net_tx,
                        net_io_recv_bytes=net_rx,
                        disk_read_bytes=blk_read,
                        disk_write_bytes=blk_write,
                        uptime_seconds=_get_uptime_seconds(container),
                    )
                )

            return DockerMetrics(
                timestamp=datetime.now(timezone.utc),
                total_containers=len(all_containers),
                running_containers=running_count,
                containers=container_metrics,
                status="ok",
            )

        except DockerException as exc:
            last_error = exc
            backoff = min(_BACKOFF_BASE * (2 ** (attempt - 1)), _BACKOFF_MAX)
            logger.warning(
                "docker_probe: tentativo %d/%d fallito (backoff %.1fs): %s",
                attempt,
                MAX_RETRIES,
                backoff,
                exc,
            )
            time.sleep(backoff)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "docker_probe: errore inatteso tentativo %d/%d: %s",
                attempt,
                MAX_RETRIES,
                exc,
            )

    logger.error(
        "docker_probe: tutti i %d tentativi falliti. Fallback degraded. "
        "Ultimo errore: %s",
        MAX_RETRIES,
        last_error,
    )
    return DockerMetrics(
        timestamp=datetime.now(timezone.utc),
        total_containers=0,
        running_containers=0,
        containers=[],
        status="degraded",
    )


async def collect_docker_metrics() -> DockerMetrics:
    """Wrapper asincrono non bloccante per la raccolta metriche Docker.

    Esegue il polling sincrono in un thread separato tramite
    asyncio.to_thread() per non bloccare l'event loop FastAPI.

    Returns:
        DockerMetrics validato tramite Pydantic.
    """
    return await asyncio.to_thread(collect_docker_metrics_sync)


def listen_docker_events(
    callback: Callable[[dict[str, Any]], None],
    event_filters: Optional[dict[str, list[str]]] = None,
) -> None:
    """Ascolta il Docker event stream in modo bloccante (da eseguire in un thread).

    Filtra per eventi container (start, stop, die) e invoca il callback
    per ogni evento ricevuto. Implementa reconnect con backoff esponenziale.

    Args:
        callback: Funzione invocata per ogni evento Docker ricevuto.
        event_filters: Filtri Docker opzionali. Default: container start/stop/die.

    Raises:
        Nessuna: gli errori vengono loggati e il listener si riconnette.
    """
    if event_filters is None:
        event_filters = {
            "type": ["container"],
            "event": ["start", "stop", "die"],
        }

    consecutive_failures = 0

    while True:
        try:
            client = _create_client()
            logger.info("docker_probe: event listener connesso al Docker Engine")
            consecutive_failures = 0

            for event in client.events(decode=True, filters=event_filters):
                try:
                    callback(event)
                except Exception as cb_exc:
                    logger.error("docker_probe: errore nel callback evento: %s", cb_exc)

        except DockerException as exc:
            consecutive_failures += 1
            backoff = min(
                _BACKOFF_BASE * (2 ** (consecutive_failures - 1)), _BACKOFF_MAX
            )
            logger.warning(
                "docker_probe: event stream disconnesso (retry #%d, backoff %.1fs): %s",
                consecutive_failures,
                backoff,
                exc,
            )
            time.sleep(backoff)

        except Exception as exc:
            consecutive_failures += 1
            backoff = min(
                _BACKOFF_BASE * (2 ** (consecutive_failures - 1)), _BACKOFF_MAX
            )
            logger.error(
                "docker_probe: errore inatteso event stream (retry #%d): %s",
                consecutive_failures,
                exc,
            )
            time.sleep(backoff)
