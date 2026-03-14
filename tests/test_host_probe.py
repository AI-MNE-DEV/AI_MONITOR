"""Test unitari per host_probe.py.

Verifica la generazione di HostMetrics valide sia in happy path
che in caso di fallimento psutil (graceful degradation).
"""

import asyncio
from unittest.mock import MagicMock, patch

from contracts import HostMetrics
from host_probe import (
    collect_host_metrics_sync,
    collect_host_metrics,
    _collect_cpu_percent,
    _collect_ram_percent,
    _collect_disk_io,
    MAX_RETRIES,
)


class TestCollectCpuPercent:
    """Test per la funzione di lettura CPU."""

    def test_returns_float(self) -> None:
        result = _collect_cpu_percent(interval=0.0)
        assert isinstance(result, float)
        assert 0.0 <= result <= 100.0


class TestCollectRamPercent:
    """Test per la funzione di lettura RAM."""

    def test_returns_float(self) -> None:
        result = _collect_ram_percent()
        assert isinstance(result, float)
        assert 0.0 <= result <= 100.0


class TestCollectDiskIo:
    """Test per la funzione di lettura I/O disco."""

    def test_returns_tuple_of_ints(self) -> None:
        read_b, write_b = _collect_disk_io()
        assert isinstance(read_b, int)
        assert isinstance(write_b, int)
        assert read_b >= 0
        assert write_b >= 0

    @patch("host_probe.psutil.disk_io_counters", return_value=None)
    def test_returns_zeros_when_counters_unavailable(
        self, mock_counters: MagicMock
    ) -> None:
        read_b, write_b = _collect_disk_io()
        assert read_b == 0
        assert write_b == 0


class TestCollectHostMetricsSync:
    """Test per la raccolta sincrona completa con retry e fallback."""

    def test_happy_path_returns_valid_host_metrics(self) -> None:
        metrics = collect_host_metrics_sync()
        assert isinstance(metrics, HostMetrics)
        assert metrics.status == "ok"
        assert 0.0 <= metrics.cpu_percent <= 100.0
        assert 0.0 <= metrics.ram_percent <= 100.0
        assert metrics.disk_io_read_bytes >= 0
        assert metrics.disk_io_write_bytes >= 0

    @patch("host_probe._collect_cpu_percent", side_effect=OSError("permesso negato"))
    def test_fallback_degraded_on_total_failure(self, mock_cpu: MagicMock) -> None:
        metrics = collect_host_metrics_sync()
        assert isinstance(metrics, HostMetrics)
        assert metrics.status == "degraded"
        assert metrics.cpu_percent == 0.0
        assert metrics.ram_percent == 0.0
        assert mock_cpu.call_count == MAX_RETRIES

    @patch("host_probe._collect_cpu_percent")
    def test_retry_succeeds_on_second_attempt(self, mock_cpu: MagicMock) -> None:
        mock_cpu.side_effect = [OSError("transient"), 42.5]
        metrics = collect_host_metrics_sync()
        assert isinstance(metrics, HostMetrics)
        assert metrics.status == "ok"
        assert metrics.cpu_percent == 42.5
        assert mock_cpu.call_count == 2


class TestCollectHostMetricsAsync:
    """Test per il wrapper asincrono."""

    def test_async_returns_valid_host_metrics(self) -> None:
        metrics = asyncio.run(collect_host_metrics())
        assert isinstance(metrics, HostMetrics)
        assert metrics.status == "ok"


class TestHostMetricsContract:
    """Test di conformità ai data contracts Pydantic."""

    def test_metrics_serializable_to_json(self) -> None:
        metrics = collect_host_metrics_sync()
        json_str = metrics.model_dump_json()
        assert "cpu_percent" in json_str
        assert "ram_percent" in json_str
        assert "disk_io_read_bytes" in json_str
        assert "status" in json_str

    def test_metrics_roundtrip(self) -> None:
        metrics = collect_host_metrics_sync()
        data = metrics.model_dump()
        restored = HostMetrics(**data)
        assert restored.cpu_percent == metrics.cpu_percent
        assert restored.ram_percent == metrics.ram_percent
