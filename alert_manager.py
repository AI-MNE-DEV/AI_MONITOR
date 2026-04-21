"""
Alert Manager - Motore a regole per la valutazione soglie e generazione allarmi.

Valuta le metriche Host e Docker in ingresso contro soglie configurabili.
Genera AlertEvent Pydantic con deduplicazione tramite cooldown.
Scrive allarmi critici su CRITICAL_ALERTS.txt come fallback.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from contracts import AlertEvent, DockerMetrics, HostMetrics

logger = logging.getLogger(__name__)

# Soglie configurabili via env con default sicuri
CPU_WARNING_THRESHOLD: float = float(os.getenv("ALERT_CPU_WARNING", "90.0"))
CPU_CRITICAL_THRESHOLD: float = float(os.getenv("ALERT_CPU_CRITICAL", "95.0"))
RAM_WARNING_THRESHOLD: float = float(os.getenv("ALERT_RAM_WARNING", "85.0"))
RAM_CRITICAL_THRESHOLD: float = float(os.getenv("ALERT_RAM_CRITICAL", "90.0"))
DISK_WARNING_THRESHOLD: float = float(os.getenv("ALERT_DISK_WARNING", "85.0"))
DISK_CRITICAL_THRESHOLD: float = float(os.getenv("ALERT_DISK_CRITICAL", "90.0"))
CONTAINER_RAM_WARNING_THRESHOLD: float = float(os.getenv("ALERT_CONTAINER_RAM_WARNING", "85.0"))
CONTAINER_RAM_CRITICAL_THRESHOLD: float = float(os.getenv("ALERT_CONTAINER_RAM_CRITICAL", "90.0"))

# Cooldown in secondi per evitare allarmi duplicati sulla stessa condizione
ALERT_COOLDOWN_SECONDS: int = int(os.getenv("ALERT_COOLDOWN_SECONDS", "60"))

# Path del file di fallback per allarmi critici
CRITICAL_ALERTS_PATH: str = os.getenv("CRITICAL_ALERTS_PATH", "CRITICAL_ALERTS.txt")


def _generate_alert_id() -> str:
    """Genera un ID univoco per un alert.

    Returns:
        Stringa UUID4 troncata a 12 caratteri.
    """
    return uuid.uuid4().hex[:12]


def _write_critical_alert_file(alert: AlertEvent) -> None:
    """Scrive un allarme critico sul file di fallback CRITICAL_ALERTS.txt.

    Usato quando il DB non è disponibile o come registro permanente
    degli eventi critici.

    Args:
        alert: Evento allarme da registrare.
    """
    try:
        os.makedirs(os.path.dirname(CRITICAL_ALERTS_PATH) or ".", exist_ok=True)
        with open(CRITICAL_ALERTS_PATH, "a", encoding="utf-8") as f:
            f.write(
                f"[{alert.timestamp.isoformat()}] "
                f"{alert.level} | {alert.source} | "
                f"{alert.message} | value={alert.metric_value}\n"
            )
    except Exception as exc:
        logger.error(
            "alert_manager: impossibile scrivere su %s: %s",
            CRITICAL_ALERTS_PATH,
            exc,
        )


class AlertManager:
    """Motore di valutazione soglie con deduplicazione e fallback su file.

    Mantiene uno stato interno dei cooldown per evitare di generare
    allarmi duplicati per la stessa condizione persistente.

    Args:
        cpu_warning: Soglia CPU warning (default da env).
        cpu_critical: Soglia CPU critical (default da env).
        ram_warning: Soglia RAM warning (default da env).
        ram_critical: Soglia RAM critical (default da env).
        cooldown_seconds: Secondi di cooldown tra allarmi uguali.
    """

    def __init__(
        self,
        cpu_warning: float = CPU_WARNING_THRESHOLD,
        cpu_critical: float = CPU_CRITICAL_THRESHOLD,
        ram_warning: float = RAM_WARNING_THRESHOLD,
        ram_critical: float = RAM_CRITICAL_THRESHOLD,
        disk_warning: float = DISK_WARNING_THRESHOLD,
        disk_critical: float = DISK_CRITICAL_THRESHOLD,
        container_ram_warning: float = CONTAINER_RAM_WARNING_THRESHOLD,
        container_ram_critical: float = CONTAINER_RAM_CRITICAL_THRESHOLD,
        cooldown_seconds: int = ALERT_COOLDOWN_SECONDS,
    ) -> None:
        self._cpu_warning: float = cpu_warning
        self._cpu_critical: float = cpu_critical
        self._ram_warning: float = ram_warning
        self._ram_critical: float = ram_critical
        self._disk_warning: float = disk_warning
        self._disk_critical: float = disk_critical
        self._container_ram_warning: float = container_ram_warning
        self._container_ram_critical: float = container_ram_critical
        self._cooldown_seconds: int = cooldown_seconds
        self._last_fired: dict[str, datetime] = {}
        self._active_alerts: list[AlertEvent] = []

    @property
    def active_alerts(self) -> list[AlertEvent]:
        """Lista degli allarmi attivi correnti."""
        return list(self._active_alerts)

    def _is_in_cooldown(self, alert_key: str, now: datetime) -> bool:
        """Verifica se un tipo di allarme è ancora in cooldown.

        Args:
            alert_key: Chiave univoca del tipo di allarme (es. "host_cpu_critical").
            now: Timestamp corrente.

        Returns:
            True se l'allarme è in cooldown e non deve essere rigenerato.
        """
        last = self._last_fired.get(alert_key)
        if last is None:
            return False
        elapsed = (now - last).total_seconds()
        return elapsed < self._cooldown_seconds

    def _fire_alert(
        self,
        alert_key: str,
        level: str,
        source: str,
        message: str,
        metric_value: Optional[float],
        now: datetime,
    ) -> Optional[AlertEvent]:
        """Genera un AlertEvent se non in cooldown.

        Args:
            alert_key: Chiave per deduplicazione.
            level: Livello allarme (WARNING, CRITICAL).
            source: Sorgente (host, docker).
            message: Descrizione dell'allarme.
            metric_value: Valore metrica che ha innescato l'allarme.
            now: Timestamp corrente.

        Returns:
            AlertEvent generato, o None se in cooldown.
        """
        if self._is_in_cooldown(alert_key, now):
            return None

        alert = AlertEvent(
            id=_generate_alert_id(),
            timestamp=now,
            level=level,
            source=source,
            message=message,
            metric_value=metric_value,
        )

        self._last_fired[alert_key] = now
        self._active_alerts.append(alert)
        logger.warning(
            "alert_manager: %s - %s (value=%s)", level, message, metric_value
        )

        if level == "CRITICAL":
            _write_critical_alert_file(alert)

        return alert

    def evaluate_host_metrics(self, metrics: HostMetrics) -> list[AlertEvent]:
        """Valuta le metriche host e genera allarmi se superano le soglie.

        Args:
            metrics: Metriche host validate da Pydantic.

        Returns:
            Lista di AlertEvent generati (può essere vuota).
        """
        alerts: list[AlertEvent] = []
        now = datetime.now(timezone.utc)

        # CPU Critical
        if metrics.cpu_percent >= self._cpu_critical:
            alert = self._fire_alert(
                alert_key="host_cpu_critical",
                level="CRITICAL",
                source="host",
                message=f"CPU al {metrics.cpu_percent}% (soglia critica: {self._cpu_critical}%)",
                metric_value=metrics.cpu_percent,
                now=now,
            )
            if alert:
                alerts.append(alert)
        # CPU Warning
        elif metrics.cpu_percent >= self._cpu_warning:
            alert = self._fire_alert(
                alert_key="host_cpu_warning",
                level="WARNING",
                source="host",
                message=f"CPU al {metrics.cpu_percent}% (soglia warning: {self._cpu_warning}%)",
                metric_value=metrics.cpu_percent,
                now=now,
            )
            if alert:
                alerts.append(alert)

        # RAM Critical
        if metrics.ram_percent >= self._ram_critical:
            alert = self._fire_alert(
                alert_key="host_ram_critical",
                level="CRITICAL",
                source="host",
                message=f"RAM al {metrics.ram_percent}% (soglia critica: {self._ram_critical}%)",
                metric_value=metrics.ram_percent,
                now=now,
            )
            if alert:
                alerts.append(alert)
        # RAM Warning
        elif metrics.ram_percent >= self._ram_warning:
            alert = self._fire_alert(
                alert_key="host_ram_warning",
                level="WARNING",
                source="host",
                message=f"RAM al {metrics.ram_percent}% (soglia warning: {self._ram_warning}%)",
                metric_value=metrics.ram_percent,
                now=now,
            )
            if alert:
                alerts.append(alert)

        # Disk Critical
        if metrics.disk_percent >= self._disk_critical:
            alert = self._fire_alert(
                alert_key="host_disk_critical",
                level="CRITICAL",
                source="host",
                message=f"Spazio Disco al {metrics.disk_percent}% (soglia critica: {self._disk_critical}%)",
                metric_value=metrics.disk_percent,
                now=now,
            )
            if alert:
                alerts.append(alert)
        # Disk Warning
        elif metrics.disk_percent >= self._disk_warning:
            alert = self._fire_alert(
                alert_key="host_disk_warning",
                level="WARNING",
                source="host",
                message=f"Spazio Disco al {metrics.disk_percent}% (soglia warning: {self._disk_warning}%)",
                metric_value=metrics.disk_percent,
                now=now,
            )
            if alert:
                alerts.append(alert)

        # Degraded status
        if metrics.status == "degraded":
            alert = self._fire_alert(
                alert_key="host_degraded",
                level="WARNING",
                source="host",
                message="Host probe in stato degraded: metriche non affidabili",
                metric_value=None,
                now=now,
            )
            if alert:
                alerts.append(alert)

        return alerts

    def evaluate_docker_metrics(self, metrics: DockerMetrics) -> list[AlertEvent]:
        """Valuta le metriche Docker e genera allarmi per stati anomali.

        Args:
            metrics: Metriche Docker validate da Pydantic.

        Returns:
            Lista di AlertEvent generati (può essere vuota).
        """
        alerts: list[AlertEvent] = []
        now = datetime.now(timezone.utc)

        # Docker degraded
        if metrics.status == "degraded":
            alert = self._fire_alert(
                alert_key="docker_degraded",
                level="CRITICAL",
                source="docker",
                message="Docker Engine non raggiungibile: socket disconnesso",
                metric_value=None,
                now=now,
            )
            if alert:
                alerts.append(alert)

        # Container con CPU/RAM alta
        for container in metrics.containers:
            if container.status == "running":
                if container.cpu_percent >= self._cpu_critical:
                    alert = self._fire_alert(
                        alert_key=f"container_cpu_{container.container_id}",
                        level="WARNING",
                        source="docker",
                        message=(
                            f"Container '{container.name}' CPU al {container.cpu_percent}%"
                        ),
                        metric_value=container.cpu_percent,
                        now=now,
                    )
                    if alert:
                        alerts.append(alert)

                if container.ram_percent >= self._container_ram_critical:
                    alert = self._fire_alert(
                        alert_key=f"container_ram_critical_{container.container_id}",
                        level="CRITICAL",
                        source="docker",
                        message=(
                            f"Container '{container.name}' RAM al {container.ram_percent}% "
                            f"(soglia critica: {self._container_ram_critical}%)"
                        ),
                        metric_value=container.ram_percent,
                        now=now,
                    )
                    if alert:
                        alerts.append(alert)
                elif container.ram_percent >= self._container_ram_warning:
                    alert = self._fire_alert(
                        alert_key=f"container_ram_warning_{container.container_id}",
                        level="WARNING",
                        source="docker",
                        message=(
                            f"Container '{container.name}' RAM al {container.ram_percent}% "
                            f"(soglia warning: {self._container_ram_warning}%)"
                        ),
                        metric_value=container.ram_percent,
                        now=now,
                    )
                    if alert:
                        alerts.append(alert)

        return alerts

    def evaluate_docker_event(self, event: dict) -> Optional[AlertEvent]:
        """Valuta un singolo evento dallo stream Docker e genera allarmi istantanei.

        Args:
            event: Dizionario raw dell'evento Docker (decode=True).

        Returns:
            AlertEvent generato se l'evento è critico, altrimenti None.
        """
        now = datetime.now(timezone.utc)
        action = event.get("Action", "")
        actor = event.get("Actor", {})
        attributes = actor.get("Attributes", {})
        container_name = attributes.get("name", "unknown")
        container_id = actor.get("ID", "unknown")[:12]

        # Container crash (die con exit code != 0)
        if action == "die":
            exit_code = attributes.get("exitCode", "0")
            if exit_code != "0":
                return self._fire_alert(
                    alert_key=f"container_crash_{container_id}_{now.timestamp()}",
                    level="CRITICAL",
                    source="docker",
                    message=f"Container '{container_name}' terminato in modo anomalo (exit code {exit_code})",
                    metric_value=None,
                    now=now,
                )

        # Out Of Memory
        elif action == "oom":
            return self._fire_alert(
                alert_key=f"container_oom_{container_id}_{now.timestamp()}",
                level="CRITICAL",
                source="docker",
                message=f"Container '{container_name}' ha esaurito la memoria (OOM Killed)",
                metric_value=None,
                now=now,
            )

        # Healthcheck fallito
        elif action.startswith("health_status: unhealthy"):
            return self._fire_alert(
                alert_key=f"container_unhealthy_{container_id}_{now.timestamp()}",
                level="WARNING",
                source="docker",
                message=f"Container '{container_name}' risulta unhealthy",
                metric_value=None,
                now=now,
            )

        return None

    def clear_resolved(self) -> None:
        """Rimuove gli allarmi attivi che non sono più in condizione critica.

        Da chiamare periodicamente per mantenere pulita la lista active_alerts.
        """
        now = datetime.now(timezone.utc)
        self._active_alerts = [
            a
            for a in self._active_alerts
            if (now - a.timestamp).total_seconds() < self._cooldown_seconds * 2
        ]
