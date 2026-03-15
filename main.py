"""
AI MONITOR - Entrypoint FastAPI.

Orchestrazione dei probe, storage, alert e WebSocket streaming.
Espone endpoint REST per storico metriche e stato allarmi,
e un endpoint WebSocket per lo streaming real-time alla War Room.
"""

import asyncio
import logging
import os
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from alert_manager import AlertManager
from contracts import DockerMetrics, HostMetrics, SystemStatus
from docker_probe import collect_docker_metrics, listen_docker_events
from host_probe import collect_host_metrics
from notifier import Notifier
from storage_engine import StorageEngine
from ws_streamer import manager

logger = logging.getLogger(__name__)

# Intervallo di polling in secondi
_POLL_INTERVAL: float = float(os.getenv("AI_MONITOR_POLL_INTERVAL", "5.0"))

# Stato globale condiviso tra i task
_latest_host: HostMetrics | None = None
_latest_docker: DockerMetrics | None = None

# Componenti core
_storage: StorageEngine | None = None
_alert_mgr: AlertManager | None = None
_notifier: Notifier | None = None


async def _telemetry_loop() -> None:
    """Loop principale di raccolta metriche, alerting, storage e broadcast.

    Eseguito come task asyncio in background. Raccoglie metriche host e Docker,
    valuta gli allarmi, persiste su DB e invia via WebSocket.
    """
    global _latest_host, _latest_docker

    while True:
        try:
            host_metrics, docker_metrics = await asyncio.gather(
                collect_host_metrics(),
                collect_docker_metrics(),
            )

            _latest_host = host_metrics
            _latest_docker = docker_metrics

            # Alerting
            alerts = []
            if _alert_mgr:
                alerts.extend(_alert_mgr.evaluate_host_metrics(host_metrics))
                alerts.extend(_alert_mgr.evaluate_docker_metrics(docker_metrics))
                _alert_mgr.clear_resolved()

            # Storage
            if _storage:
                _storage.store(host_metrics)
                _storage.store(docker_metrics)
                for alert in alerts:
                    _storage.store(alert)

            # Notifiche esterne
            if _notifier:
                for alert in alerts:
                    await _notifier.notify(alert)

            # WebSocket broadcast
            status = SystemStatus(
                host=host_metrics,
                docker=docker_metrics,
                active_alerts=_alert_mgr.active_alerts if _alert_mgr else [],
            )
            await manager.broadcast_system_status(status)

            for alert in alerts:
                await manager.broadcast_alert(alert)

        except Exception as exc:
            logger.error("main: errore nel loop telemetrico: %s", exc)

        await asyncio.sleep(_POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifecycle manager: avvia e ferma i componenti core."""
    global _storage, _alert_mgr, _notifier

    logger.info("AI MONITOR Avviato. Inizializzazione moduli Core...")

    # Storage
    _storage = StorageEngine()
    _storage.start()

    # Alert Manager
    _alert_mgr = AlertManager()

    # Notifier (notifiche esterne)
    _notifier = Notifier()
    await _notifier.start()

    # Telemetry loop
    telemetry_task = asyncio.create_task(_telemetry_loop())

    # Docker event listener in thread separato
    event_thread = threading.Thread(
        target=listen_docker_events,
        args=(lambda event: logger.info("docker_event: %s", event),),
        daemon=True,
        name="docker-event-listener",
    )
    event_thread.start()

    yield

    # Shutdown
    telemetry_task.cancel()
    try:
        await telemetry_task
    except asyncio.CancelledError:
        pass

    if _notifier:
        await _notifier.stop()

    if _storage:
        _storage.stop()

    logger.info("AI MONITOR Fermato.")


app = FastAPI(
    title="AI MONITOR",
    description=(
        "Microservizio telemetrico in tempo reale per Host e Docker, "
        "integrato in una Dashboard 'War Room'."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


_STATIC_DIR: Path = Path(__file__).parent / "static"


@app.get("/")
async def serve_dashboard() -> FileResponse:
    """Serve la War Room Dashboard SPA."""
    return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Endpoint passivo per il controllo dello stato di base dell'API (liveness probe)."""
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/v1/status", response_model=SystemStatus)
async def get_current_status() -> SystemStatus:
    """Ritorna lo snapshot istantaneo della telemetria corrente."""
    host = _latest_host or HostMetrics(
        cpu_percent=0.0, ram_percent=0.0, status="pending"
    )
    docker = _latest_docker or DockerMetrics(
        total_containers=0, running_containers=0, status="pending"
    )
    active = _alert_mgr.active_alerts if _alert_mgr else []
    return SystemStatus(host=host, docker=docker, active_alerts=active)


@app.get("/api/v1/history/host")
async def get_host_history(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, object]]:
    """Ritorna lo storico delle metriche host dal DB.

    Args:
        limit: Numero massimo di record (default 100, max 1000).
        offset: Offset per paginazione.

    Returns:
        Lista di record metriche host ordinati per timestamp DESC.
    """
    if not _storage or not _storage._db_path:
        return []

    try:
        conn = sqlite3.connect(_storage._db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM host_metrics ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.error("main: errore query storico host: %s", exc)
        return []


@app.get("/api/v1/history/docker")
async def get_docker_history(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, object]]:
    """Ritorna lo storico delle metriche Docker dal DB.

    Args:
        limit: Numero massimo di record (default 100, max 1000).
        offset: Offset per paginazione.

    Returns:
        Lista di record metriche Docker ordinati per timestamp DESC.
    """
    if not _storage or not _storage._db_path:
        return []

    try:
        conn = sqlite3.connect(_storage._db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM docker_metrics ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.error("main: errore query storico docker: %s", exc)
        return []


@app.get("/api/v1/alerts")
async def get_alerts(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, object]]:
    """Ritorna lo storico degli allarmi dal DB.

    Args:
        limit: Numero massimo di record (default 50, max 500).
        offset: Offset per paginazione.

    Returns:
        Lista di record allarmi ordinati per timestamp DESC.
    """
    if not _storage or not _storage._db_path:
        return []

    try:
        conn = sqlite3.connect(_storage._db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM alert_events ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.error("main: errore query allarmi: %s", exc)
        return []


@app.get("/api/v1/alerts/active")
async def get_active_alerts() -> list[dict[str, object]]:
    """Ritorna gli allarmi attualmente attivi (in-memory)."""
    if not _alert_mgr:
        return []
    return [a.model_dump(mode="json") for a in _alert_mgr.active_alerts]


@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket) -> None:
    """Endpoint WebSocket per lo streaming real-time alla War Room.

    Il client riceve automaticamente i broadcast dal ConnectionManager.
    Può inviare messaggi di controllo (ping/pong).
    """
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
