"""
WS Streamer - Modulo di diffusione telemetria in real-time via WebSocket.

Gestisce le connessioni WebSocket dei client frontend, il broadcast
di SystemStatus e AlertEvent, e il tracking delle sessioni attive.
Client lenti o disconnessi vengono rimossi senza impattare gli altri.
"""

import asyncio
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from contracts import AlertEvent, SystemStatus

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Gestore delle connessioni WebSocket attive con broadcast non bloccante.

    Traccia i client connessi e fornisce broadcast asincrono.
    Client che non rispondono vengono rimossi automaticamente.
    """

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    @property
    def active_count(self) -> int:
        """Numero di client WebSocket attualmente connessi."""
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        """Accetta e registra una nuova connessione WebSocket.

        Args:
            websocket: Connessione WebSocket da registrare.
        """
        await websocket.accept()
        self._connections.append(websocket)
        logger.info(
            "ws_streamer: client connesso. Totale attivi: %d",
            self.active_count,
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Rimuove un client disconnesso dalla lista.

        Args:
            websocket: Connessione WebSocket da rimuovere.
        """
        if websocket in self._connections:
            self._connections.remove(websocket)
        logger.info(
            "ws_streamer: client disconnesso. Totale attivi: %d",
            self.active_count,
        )

    async def broadcast_json(self, data: dict[str, Any]) -> None:
        """Invia un payload JSON a tutti i client connessi.

        Client che falliscono vengono rimossi automaticamente.

        Args:
            data: Dizionario JSON-serializzabile da inviare.
        """
        stale: list[WebSocket] = []

        for ws in self._connections:
            try:
                await asyncio.wait_for(ws.send_json(data), timeout=5.0)
            except (WebSocketDisconnect, RuntimeError, asyncio.TimeoutError) as exc:
                logger.warning("ws_streamer: rimozione client stale: %s", exc)
                stale.append(ws)
            except Exception as exc:
                logger.error("ws_streamer: errore broadcast inatteso: %s", exc)
                stale.append(ws)

        for ws in stale:
            self.disconnect(ws)

    async def broadcast_system_status(self, status: SystemStatus) -> None:
        """Broadcast dello stato completo del sistema a tutti i client.

        Args:
            status: SystemStatus Pydantic validato.
        """
        payload = {
            "type": "system_status",
            "data": status.model_dump(mode="json"),
        }
        await self.broadcast_json(payload)

    async def broadcast_alert(self, alert: AlertEvent) -> None:
        """Broadcast di un singolo allarme a tutti i client.

        Args:
            alert: AlertEvent Pydantic validato.
        """
        payload = {
            "type": "alert",
            "data": alert.model_dump(mode="json"),
        }
        await self.broadcast_json(payload)


# Istanza singleton del connection manager
manager = ConnectionManager()
