"""Test unitari per alert_manager.py.

Verifica la valutazione soglie, deduplicazione cooldown,
generazione AlertEvent e scrittura su CRITICAL_ALERTS.txt.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from contracts import AlertEvent, ContainerMetrics, DockerMetrics, HostMetrics
from alert_manager import AlertManager, _write_critical_alert_file


def _host(cpu: float = 10.0, ram: float = 30.0, status: str = "ok") -> HostMetrics:
    """Helper per creare HostMetrics di test."""
    return HostMetrics(
        timestamp=datetime.now(timezone.utc),
        cpu_percent=cpu,
        ram_percent=ram,
        status=status,
    )


def _docker(
    status: str = "ok",
    containers: list[ContainerMetrics] | None = None,
) -> DockerMetrics:
    """Helper per creare DockerMetrics di test."""
    return DockerMetrics(
        timestamp=datetime.now(timezone.utc),
        total_containers=len(containers or []),
        running_containers=sum(1 for c in (containers or []) if c.status == "running"),
        containers=containers or [],
        status=status,
    )


class TestEvaluateHostMetrics:
    """Test per la valutazione soglie host."""

    def test_no_alert_below_thresholds(self) -> None:
        mgr = AlertManager(cpu_warning=90.0, cpu_critical=95.0)
        alerts = mgr.evaluate_host_metrics(_host(cpu=50.0, ram=40.0))
        assert alerts == []

    def test_cpu_warning_fires(self) -> None:
        mgr = AlertManager(cpu_warning=90.0, cpu_critical=95.0, cooldown_seconds=0)
        alerts = mgr.evaluate_host_metrics(_host(cpu=92.0))
        assert len(alerts) == 1
        assert alerts[0].level == "WARNING"
        assert "CPU" in alerts[0].message
        assert alerts[0].metric_value == 92.0

    def test_cpu_critical_fires(self) -> None:
        mgr = AlertManager(cpu_warning=90.0, cpu_critical=95.0, cooldown_seconds=0)
        alerts = mgr.evaluate_host_metrics(_host(cpu=97.0))
        assert len(alerts) == 1
        assert alerts[0].level == "CRITICAL"
        assert alerts[0].source == "host"

    def test_ram_warning_fires(self) -> None:
        mgr = AlertManager(ram_warning=85.0, ram_critical=90.0, cooldown_seconds=0)
        alerts = mgr.evaluate_host_metrics(_host(ram=87.0))
        assert len(alerts) == 1
        assert alerts[0].level == "WARNING"
        assert "RAM" in alerts[0].message

    def test_ram_critical_fires(self) -> None:
        mgr = AlertManager(ram_warning=85.0, ram_critical=90.0, cooldown_seconds=0)
        alerts = mgr.evaluate_host_metrics(_host(ram=95.0))
        assert len(alerts) == 1
        assert alerts[0].level == "CRITICAL"

    def test_both_cpu_and_ram_fire(self) -> None:
        mgr = AlertManager(
            cpu_warning=90.0,
            cpu_critical=95.0,
            ram_warning=85.0,
            ram_critical=90.0,
            cooldown_seconds=0,
        )
        alerts = mgr.evaluate_host_metrics(_host(cpu=96.0, ram=92.0))
        assert len(alerts) == 2
        levels = {a.level for a in alerts}
        assert levels == {"CRITICAL"}

    def test_degraded_status_fires_warning(self) -> None:
        mgr = AlertManager(cooldown_seconds=0)
        alerts = mgr.evaluate_host_metrics(_host(cpu=10.0, ram=10.0, status="degraded"))
        assert len(alerts) == 1
        assert alerts[0].level == "WARNING"
        assert "degraded" in alerts[0].message


class TestCooldown:
    """Test per la deduplicazione tramite cooldown."""

    def test_cooldown_prevents_duplicate(self) -> None:
        mgr = AlertManager(cpu_warning=90.0, cpu_critical=95.0, cooldown_seconds=60)
        a1 = mgr.evaluate_host_metrics(_host(cpu=92.0))
        a2 = mgr.evaluate_host_metrics(_host(cpu=93.0))
        assert len(a1) == 1
        assert len(a2) == 0  # in cooldown

    def test_alert_fires_after_cooldown_expires(self) -> None:
        mgr = AlertManager(cpu_warning=90.0, cpu_critical=95.0, cooldown_seconds=60)
        a1 = mgr.evaluate_host_metrics(_host(cpu=92.0))
        assert len(a1) == 1

        # Simula che il cooldown è scaduto spostando il timestamp indietro
        mgr._last_fired["host_cpu_warning"] = datetime.now(timezone.utc) - timedelta(
            seconds=120
        )
        a2 = mgr.evaluate_host_metrics(_host(cpu=92.0))
        assert len(a2) == 1

    def test_different_alert_keys_not_affected(self) -> None:
        mgr = AlertManager(
            cpu_warning=90.0,
            cpu_critical=95.0,
            ram_warning=85.0,
            ram_critical=90.0,
            cooldown_seconds=60,
        )
        a1 = mgr.evaluate_host_metrics(_host(cpu=92.0, ram=87.0))
        # CPU warning + RAM warning = 2 allarmi distinti
        assert len(a1) == 2


class TestEvaluateDockerMetrics:
    """Test per la valutazione metriche Docker."""

    def test_no_alert_on_healthy_docker(self) -> None:
        mgr = AlertManager(cooldown_seconds=0)
        alerts = mgr.evaluate_docker_metrics(_docker())
        assert alerts == []

    def test_degraded_docker_fires_critical(self) -> None:
        mgr = AlertManager(cooldown_seconds=0)
        alerts = mgr.evaluate_docker_metrics(_docker(status="degraded"))
        assert len(alerts) == 1
        assert alerts[0].level == "CRITICAL"
        assert alerts[0].source == "docker"

    def test_container_high_cpu_fires_warning(self) -> None:
        mgr = AlertManager(cpu_critical=95.0, cooldown_seconds=0)
        container = ContainerMetrics(
            container_id="abc123",
            name="hot-container",
            status="running",
            cpu_percent=98.0,
            ram_usage_mb=512.0,
        )
        alerts = mgr.evaluate_docker_metrics(_docker(containers=[container]))
        assert len(alerts) == 1
        assert "hot-container" in alerts[0].message

    def test_exited_container_no_cpu_alert(self) -> None:
        mgr = AlertManager(cpu_critical=95.0, cooldown_seconds=0)
        container = ContainerMetrics(
            container_id="abc123",
            name="dead",
            status="exited",
            cpu_percent=99.0,
        )
        alerts = mgr.evaluate_docker_metrics(_docker(containers=[container]))
        assert alerts == []


class TestActiveAlerts:
    """Test per la gestione lista allarmi attivi."""

    def test_active_alerts_populated(self) -> None:
        mgr = AlertManager(cpu_warning=90.0, cooldown_seconds=0)
        mgr.evaluate_host_metrics(_host(cpu=92.0))
        assert len(mgr.active_alerts) == 1

    def test_clear_resolved_removes_old(self) -> None:
        mgr = AlertManager(cpu_warning=90.0, cooldown_seconds=1)
        mgr.evaluate_host_metrics(_host(cpu=92.0))
        assert len(mgr.active_alerts) == 1

        # Forza il timestamp dell'allarme nel passato
        mgr._active_alerts[0] = AlertEvent(
            id=mgr._active_alerts[0].id,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=300),
            level="WARNING",
            source="host",
            message="old alert",
        )
        mgr.clear_resolved()
        assert len(mgr.active_alerts) == 0


class TestCriticalAlertsFile:
    """Test per la scrittura su CRITICAL_ALERTS.txt."""

    def test_critical_alert_written_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "CRITICAL_ALERTS.txt")
            with patch("alert_manager.CRITICAL_ALERTS_PATH", path):
                mgr = AlertManager(cpu_critical=95.0, cooldown_seconds=0)
                mgr.evaluate_host_metrics(_host(cpu=97.0))

            assert os.path.exists(path)
            content = open(path, encoding="utf-8").read()
            assert "CRITICAL" in content
            assert "CPU" in content

    def test_warning_not_written_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "CRITICAL_ALERTS.txt")
            with patch("alert_manager.CRITICAL_ALERTS_PATH", path):
                mgr = AlertManager(
                    cpu_warning=90.0, cpu_critical=95.0, cooldown_seconds=0
                )
                mgr.evaluate_host_metrics(_host(cpu=92.0))

            assert not os.path.exists(path)

    def test_write_critical_alert_file_directly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test_alerts.txt")
            alert = AlertEvent(
                id="test-id",
                timestamp=datetime.now(timezone.utc),
                level="CRITICAL",
                source="host",
                message="test message",
                metric_value=99.0,
            )
            with patch("alert_manager.CRITICAL_ALERTS_PATH", path):
                _write_critical_alert_file(alert)

            content = open(path, encoding="utf-8").read()
            assert "CRITICAL" in content
            assert "test message" in content
            assert "99.0" in content


class TestAlertEventContract:
    """Test di conformità ai data contracts."""

    def test_alert_has_valid_id(self) -> None:
        mgr = AlertManager(cpu_warning=90.0, cooldown_seconds=0)
        alerts = mgr.evaluate_host_metrics(_host(cpu=92.0))
        assert len(alerts[0].id) == 12

    def test_alert_serializable(self) -> None:
        mgr = AlertManager(cpu_warning=90.0, cooldown_seconds=0)
        alerts = mgr.evaluate_host_metrics(_host(cpu=92.0))
        json_str = alerts[0].model_dump_json()
        assert "level" in json_str
        assert "source" in json_str
