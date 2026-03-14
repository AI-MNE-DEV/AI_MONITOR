import logging
from datetime import datetime, timezone
from typing import Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from contracts import HostMetrics, DockerMetrics, SystemStatus

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI MONITOR",
    description="Microservizio telemetrico in tempo reale per Host e Docker, integrato in una Dashboard 'War Room'.",
    version="1.0.0",
)


@app.on_event("startup")
async def startup_event():
    """
    Logica di startup dell'applicazione:
    Qui verranno inizializzati i probe HW e Docker in background.
    TODO: [SKILL: system_engineer, docker_expert] avviare i task asincroni/thread per il polling.
    """
    logger.info("AI MONITOR Avviato. Inizializzazione moduli Core...")


@app.get("/health", response_model=Dict[str, str])
async def health_check():
    """Endpoint passivo per il controllo dello stato di base dell'API (liveness probe)."""
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/v1/status", response_model=SystemStatus)
async def get_current_status():
    """
    Ritorna lo snapshot istantaneo della telemetria interrogando la memoria
    o i probe direttamente tramite i Contracts Pydantic.
    """
    # Mock data temporaneo per conformità al contratto in fase di setup iniziale
    host_mock = HostMetrics(cpu_percent=15.5, ram_percent=45.0)
    # Status per docker mock potrebbe essere "ok" ma testiamo la struttura
    docker_mock = DockerMetrics(
        total_containers=5, running_containers=3, containers=[], status="ok"
    )
    return SystemStatus(host=host_mock, docker=docker_mock, active_alerts=[])


@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    """
    Endpoint WebSocket che verrà contattato dal Frontend (SPA 'War Room').
    Fornirà uno stream JSON continuo (a bassa latenza) per mantenere reattiva la Dashboard.
    TODO: Implementare logic in ws_streamer.py (Task 3.2)
    """
    await websocket.accept()
    try:
        while True:
            # Semplice echo temporaneo durante il setup architetturale
            data = await websocket.receive_text()
            await websocket.send_text(f"Echo received: {data}")
    except WebSocketDisconnect:
        logger.info("Frontend Dashboard Disconnected dal flusso Telemetrico")
