"""Test per ws_streamer.py e le rotte REST/WebSocket di main.py.

Verifica il ConnectionManager, il broadcast, le rotte REST
di storico e allarmi, e il WebSocket ping/pong.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from contracts import AlertEvent, DockerMetrics, HostMetrics, SystemStatus
from ws_streamer import ConnectionManager


class TestConnectionManager:
    """Test per il gestore connessioni WebSocket."""

    def test_initial_count_is_zero(self) -> None:
        mgr = ConnectionManager()
        assert mgr.active_count == 0

    @pytest.mark.asyncio
    async def test_connect_increments_count(self) -> None:
        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws)
        assert mgr.active_count == 1
        ws.accept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_decrements_count(self) -> None:
        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws)
        mgr.disconnect(ws)
        assert mgr.active_count == 0

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_is_safe(self) -> None:
        mgr = ConnectionManager()
        ws = AsyncMock()
        mgr.disconnect(ws)  # no crash
        assert mgr.active_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_json_sends_to_all(self) -> None:
        mgr = ConnectionManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await mgr.connect(ws1)
        await mgr.connect(ws2)

        await mgr.broadcast_json({"test": "data"})

        ws1.send_json.assert_awaited_once_with({"test": "data"})
        ws2.send_json.assert_awaited_once_with({"test": "data"})

    @pytest.mark.asyncio
    async def test_broadcast_removes_stale_client(self) -> None:
        mgr = ConnectionManager()
        ws_good = AsyncMock()
        ws_bad = AsyncMock()
        ws_bad.send_json.side_effect = RuntimeError("connection closed")

        await mgr.connect(ws_good)
        await mgr.connect(ws_bad)
        assert mgr.active_count == 2

        await mgr.broadcast_json({"test": "data"})

        assert mgr.active_count == 1
        ws_good.send_json.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_broadcast_system_status(self) -> None:
        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws)

        status = SystemStatus(
            host=HostMetrics(cpu_percent=50.0, ram_percent=60.0),
            docker=DockerMetrics(total_containers=1, running_containers=1),
        )
        await mgr.broadcast_system_status(status)

        ws.send_json.assert_awaited_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "system_status"
        assert "cpu_percent" in str(payload["data"])

    @pytest.mark.asyncio
    async def test_broadcast_alert(self) -> None:
        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws)

        alert = AlertEvent(
            id="test-alert",
            level="CRITICAL",
            source="host",
            message="test",
        )
        await mgr.broadcast_alert(alert)

        ws.send_json.assert_awaited_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "alert"
        assert payload["data"]["level"] == "CRITICAL"


class TestRESTEndpoints:
    """Test per le rotte REST di main.py."""

    @pytest.fixture(autouse=True)
    def _patch_lifespan(self) -> None:
        """Disabilita il lifespan per i test REST (nessun probe reale)."""
        from contextlib import asynccontextmanager

        import main

        @asynccontextmanager
        async def noop_lifespan(app):  # type: ignore[no-untyped-def]
            yield

        main.app.router.lifespan_context = noop_lifespan

    def test_health_check(self) -> None:
        from main import app

        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "time" in data

    def test_get_current_status(self) -> None:
        from main import app

        with TestClient(app) as client:
            resp = client.get("/api/v1/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "host" in data
            assert "docker" in data
            assert "active_alerts" in data

    def test_get_host_history_empty(self) -> None:
        from main import app

        with TestClient(app) as client:
            resp = client.get("/api/v1/history/host")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_get_docker_history_empty(self) -> None:
        from main import app

        with TestClient(app) as client:
            resp = client.get("/api/v1/history/docker")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_get_alerts_empty(self) -> None:
        from main import app

        with TestClient(app) as client:
            resp = client.get("/api/v1/alerts")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_get_active_alerts_empty(self) -> None:
        from main import app

        with TestClient(app) as client:
            resp = client.get("/api/v1/alerts/active")
            assert resp.status_code == 200
            assert resp.json() == []


class TestWebSocketEndpoint:
    """Test per il WebSocket ping/pong."""

    @pytest.fixture(autouse=True)
    def _patch_lifespan(self) -> None:
        from contextlib import asynccontextmanager

        import main

        @asynccontextmanager
        async def noop_lifespan(app):  # type: ignore[no-untyped-def]
            yield

        main.app.router.lifespan_context = noop_lifespan

    def test_websocket_ping_pong(self) -> None:
        from main import app

        with TestClient(app) as client:
            with client.websocket_connect("/ws/telemetry") as ws:
                ws.send_text("ping")
                data = ws.receive_text()
                assert data == "pong"
