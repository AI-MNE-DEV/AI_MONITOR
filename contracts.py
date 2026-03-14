from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone


class HostMetrics(BaseModel):
    """Contratto dati per le metriche dell'host fisico o VM"""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cpu_percent: float = Field(..., description="Utilizzo CPU globale (0-100%)")
    ram_percent: float = Field(..., description="Utilizzo RAM globale (0-100%)")
    disk_io_read_bytes: int = Field(default=0, description="I/O in lettura globale")
    disk_io_write_bytes: int = Field(default=0, description="I/O in scrittura globale")
    status: str = Field(
        default="ok", description="Rappresenta lo stato: ok, degraded, failed"
    )


class ContainerMetrics(BaseModel):
    """Metriche di un singolo container Docker"""

    container_id: str
    name: str
    status: str = Field(..., description="Stato del container: running, exited, ghost")
    cpu_percent: float = Field(default=0.0)
    ram_usage_mb: float = Field(default=0.0)
    uptime_seconds: Optional[int] = Field(default=None)


class DockerMetrics(BaseModel):
    """Contratto dati per lo stato complessivo del sottosistema Docker"""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_containers: int
    running_containers: int
    containers: List[ContainerMetrics] = Field(default_factory=list)
    status: str = Field(
        default="ok", description="ok, degraded (socket failing), failed"
    )


class AlertEvent(BaseModel):
    """Contratto dati per gli allarmi del sistema (Active Alerting)"""

    id: str = Field(..., description="ID Univoco per tracciare l'alert")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    level: str = Field(..., description="WARNING, CRITICAL, INFO")
    source: str = Field(..., description="host, docker, database")
    message: str = Field(..., description="Dettaglio umano dell'allarme")
    metric_value: Optional[float] = Field(
        default=None, description="Valore che ha innescato l'allarme, se applicabile"
    )


class SystemStatus(BaseModel):
    """Contratto dati del payload inviato costantemente via WebSockets a bassa latenza"""

    host: HostMetrics
    docker: DockerMetrics
    active_alerts: List[AlertEvent] = Field(default_factory=list)
