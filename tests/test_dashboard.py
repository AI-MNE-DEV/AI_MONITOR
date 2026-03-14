"""Test per la War Room Dashboard SPA.

Verifica che la pagina viene servita, che il markup contiene
gli elementi essenziali della UI, e che il contrasto colori
rispetta le specifiche NOC.
"""

from contextlib import asynccontextmanager

from fastapi.testclient import TestClient


def _get_app():  # type: ignore[no-untyped-def]
    """Importa e configura l'app con lifespan disabilitato per i test."""
    import main

    @asynccontextmanager
    async def noop_lifespan(app):  # type: ignore[no-untyped-def]
        yield

    main.app.router.lifespan_context = noop_lifespan
    return main.app


class TestDashboardServing:
    """Test che la SPA viene servita correttamente."""

    def test_root_returns_html(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"]

    def test_html_contains_title(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert "<title>AI MONITOR - War Room</title>" in html

    def test_html_contains_websocket_connection(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert "new WebSocket" in html
            assert "/ws/telemetry" in html


class TestDashboardStructure:
    """Test che il markup contiene tutti gli elementi NOC richiesti."""

    def test_has_global_status_bar(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert 'id="globalStatus"' in html

    def test_has_host_metrics_section(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert 'id="cpuValue"' in html
            assert 'id="ramValue"' in html
            assert 'id="diskRead"' in html
            assert 'id="diskWrite"' in html

    def test_has_docker_section(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert 'id="totalContainers"' in html
            assert 'id="runningContainers"' in html
            assert 'id="containerList"' in html

    def test_has_alerts_section(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert 'id="alertList"' in html
            assert "ACTIVE ALERTS" in html

    def test_has_live_indicator(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert 'id="liveDot"' in html

    def test_has_clock(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert 'id="clock"' in html

    def test_no_page_reload_mechanism(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert "location.reload" not in html
            assert "window.location.href" not in html


class TestDashboardDesign:
    """Test per i requisiti di design NOC/War Room."""

    def test_dark_mode_background(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert "#0a0e17" in html  # bg-primary

    def test_semantic_colors_defined(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert "#00ff88" in html  # green (OK)
            assert "#ffaa00" in html  # yellow (WARNING)
            assert "#ff3366" in html  # red (CRITICAL)

    def test_large_typography_for_metrics(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            # Metric values devono avere font-size >= 3rem
            assert "font-size: 3rem" in html

    def test_monospace_font_for_numbers(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert "font-mono" in html
            assert "monospace" in html

    def test_reconnect_with_backoff(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert "reconnectDelay" in html
            assert "Math.min" in html  # backoff cap

    def test_xss_protection_in_js(self) -> None:
        app = _get_app()
        with TestClient(app) as client:
            html = client.get("/").text
            assert "escapeHtml" in html
