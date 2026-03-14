"""Test unitari per docker_probe.py.

Mock completo dell'SDK Docker per testare happy path,
disconnessione socket e graceful degradation.
"""

import threading
from unittest.mock import MagicMock, patch

from contracts import ContainerMetrics, DockerMetrics
from docker_probe import (
    MAX_RETRIES,
    _parse_cpu_percent,
    _parse_ram_usage_mb,
    collect_docker_metrics_sync,
    listen_docker_events,
)


def _make_fake_stats(
    cpu_total: int = 500_000,
    precpu_total: int = 400_000,
    system_cpu: int = 10_000_000,
    presystem_cpu: int = 9_000_000,
    online_cpus: int = 4,
    mem_usage: int = 104_857_600,
    mem_cache: int = 0,
) -> dict:
    """Helper per creare un dizionario stats Docker fittizio."""
    return {
        "cpu_stats": {
            "cpu_usage": {"total_usage": cpu_total, "percpu_usage": [0] * online_cpus},
            "system_cpu_usage": system_cpu,
            "online_cpus": online_cpus,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": precpu_total},
            "system_cpu_usage": presystem_cpu,
        },
        "memory_stats": {
            "usage": mem_usage,
            "stats": {"cache": mem_cache},
        },
    }


def _make_fake_container(
    short_id: str = "abc123",
    name: str = "test-container",
    status: str = "running",
    stats_data: dict | None = None,
    started_at: str = "2026-03-14T10:00:00Z",
) -> MagicMock:
    """Helper per creare un container Docker fittizio."""
    container = MagicMock()
    container.short_id = short_id
    container.name = name
    container.status = status
    container.attrs = {"State": {"StartedAt": started_at}}
    container.stats.return_value = stats_data or _make_fake_stats()
    return container


class TestParseCpuPercent:
    """Test per il calcolo CPU da stats Docker."""

    def test_calculates_cpu_percent(self) -> None:
        stats = _make_fake_stats(
            cpu_total=500_000,
            precpu_total=400_000,
            system_cpu=10_000_000,
            presystem_cpu=9_000_000,
            online_cpus=4,
        )
        result = _parse_cpu_percent(stats)
        # delta_cpu=100000, delta_system=1000000, 4 cpus -> 40.0%
        assert result == 40.0

    def test_returns_zero_on_empty_stats(self) -> None:
        assert _parse_cpu_percent({}) == 0.0

    def test_returns_zero_when_system_delta_zero(self) -> None:
        stats = _make_fake_stats(system_cpu=5_000_000, presystem_cpu=5_000_000)
        assert _parse_cpu_percent(stats) == 0.0


class TestParseRamUsageMb:
    """Test per il calcolo RAM da stats Docker."""

    def test_calculates_ram_mb(self) -> None:
        stats = _make_fake_stats(mem_usage=104_857_600, mem_cache=0)
        result = _parse_ram_usage_mb(stats)
        assert result == 100.0  # 100 MB

    def test_subtracts_cache(self) -> None:
        stats = _make_fake_stats(mem_usage=104_857_600, mem_cache=52_428_800)
        result = _parse_ram_usage_mb(stats)
        assert result == 50.0  # 50 MB

    def test_returns_zero_on_empty(self) -> None:
        assert _parse_ram_usage_mb({}) == 0.0


class TestCollectDockerMetricsSync:
    """Test per la raccolta metriche completa con mock SDK."""

    @patch("docker_probe._create_client")
    def test_happy_path_running_container(self, mock_create: MagicMock) -> None:
        container = _make_fake_container()
        client = MagicMock()
        client.containers.list.return_value = [container]
        mock_create.return_value = client

        metrics = collect_docker_metrics_sync()

        assert isinstance(metrics, DockerMetrics)
        assert metrics.status == "ok"
        assert metrics.total_containers == 1
        assert metrics.running_containers == 1
        assert len(metrics.containers) == 1
        assert isinstance(metrics.containers[0], ContainerMetrics)
        assert metrics.containers[0].name == "test-container"
        assert metrics.containers[0].cpu_percent == 40.0

    @patch("docker_probe._create_client")
    def test_mixed_running_and_exited(self, mock_create: MagicMock) -> None:
        running = _make_fake_container(short_id="r1", name="runner", status="running")
        exited = _make_fake_container(short_id="e1", name="stopped", status="exited")
        client = MagicMock()
        client.containers.list.return_value = [running, exited]
        mock_create.return_value = client

        metrics = collect_docker_metrics_sync()

        assert metrics.total_containers == 2
        assert metrics.running_containers == 1
        assert metrics.containers[1].status == "exited"
        assert metrics.containers[1].cpu_percent == 0.0

    @patch("docker_probe.time.sleep")
    @patch("docker_probe._create_client")
    def test_fallback_degraded_on_socket_failure(
        self, mock_create: MagicMock, mock_sleep: MagicMock
    ) -> None:
        from docker.errors import DockerException

        mock_create.side_effect = DockerException("socket not found")

        metrics = collect_docker_metrics_sync()

        assert isinstance(metrics, DockerMetrics)
        assert metrics.status == "degraded"
        assert metrics.total_containers == 0
        assert metrics.containers == []
        assert mock_create.call_count == MAX_RETRIES

    @patch("docker_probe.time.sleep")
    @patch("docker_probe._create_client")
    def test_retry_succeeds_on_second_attempt(
        self, mock_create: MagicMock, mock_sleep: MagicMock
    ) -> None:
        from docker.errors import DockerException

        container = _make_fake_container()
        good_client = MagicMock()
        good_client.containers.list.return_value = [container]

        mock_create.side_effect = [DockerException("transient"), good_client]

        metrics = collect_docker_metrics_sync()

        assert metrics.status == "ok"
        assert metrics.total_containers == 1
        assert mock_create.call_count == 2

    @patch("docker_probe._create_client")
    def test_stats_failure_for_single_container(self, mock_create: MagicMock) -> None:
        container = _make_fake_container()
        container.stats.side_effect = Exception("stats timeout")
        client = MagicMock()
        client.containers.list.return_value = [container]
        mock_create.return_value = client

        metrics = collect_docker_metrics_sync()

        assert metrics.status == "ok"
        assert metrics.containers[0].cpu_percent == 0.0
        assert metrics.containers[0].ram_usage_mb == 0.0

    @patch("docker_probe._create_client")
    def test_no_containers(self, mock_create: MagicMock) -> None:
        client = MagicMock()
        client.containers.list.return_value = []
        mock_create.return_value = client

        metrics = collect_docker_metrics_sync()

        assert metrics.status == "ok"
        assert metrics.total_containers == 0
        assert metrics.running_containers == 0


class TestListenDockerEvents:
    """Test per il listener event stream Docker."""

    @patch("docker_probe._create_client")
    def test_callback_invoked_on_event(self, mock_create: MagicMock) -> None:
        fake_events = [
            {"Type": "container", "Action": "start", "id": "abc123"},
            {"Type": "container", "Action": "die", "id": "def456"},
        ]
        client = MagicMock()
        client.events.return_value = iter(fake_events)
        mock_create.return_value = client

        received: list[dict] = []

        def stop_after_events(event: dict) -> None:
            received.append(event)
            if len(received) >= 2:
                raise StopIteration("test done")

        # L'evento StopIteration viene catturato dal for loop interno,
        # poi il while True ritenta la connessione. Usiamo un side_effect
        # per far fallire il secondo tentativo e uscire.
        from docker.errors import DockerException

        mock_create.side_effect = [client, DockerException("stop test")]

        # Eseguiamo in un thread con timeout per non bloccare il test
        thread = threading.Thread(
            target=listen_docker_events,
            args=(stop_after_events,),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=3.0)

        assert len(received) == 2
        assert received[0]["Action"] == "start"
        assert received[1]["Action"] == "die"


class TestDockerMetricsContract:
    """Test di conformità ai data contracts Pydantic."""

    @patch("docker_probe._create_client")
    def test_serializable_to_json(self, mock_create: MagicMock) -> None:
        container = _make_fake_container()
        client = MagicMock()
        client.containers.list.return_value = [container]
        mock_create.return_value = client

        metrics = collect_docker_metrics_sync()
        json_str = metrics.model_dump_json()

        assert "total_containers" in json_str
        assert "running_containers" in json_str
        assert "test-container" in json_str

    @patch("docker_probe._create_client")
    def test_roundtrip(self, mock_create: MagicMock) -> None:
        container = _make_fake_container()
        client = MagicMock()
        client.containers.list.return_value = [container]
        mock_create.return_value = client

        metrics = collect_docker_metrics_sync()
        data = metrics.model_dump()
        restored = DockerMetrics(**data)

        assert restored.total_containers == metrics.total_containers
        assert restored.containers[0].name == metrics.containers[0].name
