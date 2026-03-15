"""
Notifier - Dispatcher asincrono per notifiche esterne.

Invia alert critici via Telegram Bot API e/o Webhook generico (POST JSON).
Configurazione interamente via variabili d'ambiente. Non blocca mai la pipeline:
in caso di errore di rete logga un warning e prosegue.
"""

import logging
import os
from datetime import datetime, timezone

import httpx

from contracts import AlertEvent

logger = logging.getLogger(__name__)

# --- Configurazione via env ---
TELEGRAM_ENABLED: bool = os.getenv("NOTIFY_TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN: str = os.getenv("NOTIFY_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("NOTIFY_TELEGRAM_CHAT_ID", "")

WEBHOOK_ENABLED: bool = os.getenv("NOTIFY_WEBHOOK_ENABLED", "false").lower() == "true"
WEBHOOK_URL: str = os.getenv("NOTIFY_WEBHOOK_URL", "")

# Livello minimo per inviare notifiche: CRITICAL o WARNING
NOTIFY_MIN_LEVEL: str = os.getenv("NOTIFY_MIN_LEVEL", "CRITICAL").upper()

# Timeout HTTP per evitare blocchi
_HTTP_TIMEOUT: float = float(os.getenv("NOTIFY_HTTP_TIMEOUT", "10.0"))

# Ordine di severità per confronto livelli
_LEVEL_ORDER: dict[str, int] = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}


def _should_notify(alert_level: str) -> bool:
    """Verifica se il livello dell'alert raggiunge la soglia minima configurata."""
    return _LEVEL_ORDER.get(alert_level, 0) >= _LEVEL_ORDER.get(NOTIFY_MIN_LEVEL, 2)


def _format_telegram_message(alert: AlertEvent) -> str:
    """Formatta un AlertEvent come messaggio Telegram (MarkdownV2-safe plain text)."""
    icon = "🔴" if alert.level == "CRITICAL" else "🟡"
    value_str = (
        f"{alert.metric_value:.1f}%" if alert.metric_value is not None else "N/A"
    )
    return (
        f"{icon} AI MONITOR [{alert.level}]\n"
        f"Source: {alert.source}\n"
        f"Message: {alert.message}\n"
        f"Value: {value_str}\n"
        f"Time: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )


def _build_webhook_payload(alert: AlertEvent) -> dict:
    """Costruisce il payload JSON per il webhook generico."""
    return {
        "event": "ai_monitor_alert",
        "id": alert.id,
        "level": alert.level,
        "source": alert.source,
        "message": alert.message,
        "metric_value": alert.metric_value,
        "timestamp": alert.timestamp.isoformat(),
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }


class Notifier:
    """Dispatcher di notifiche esterne non-bloccante.

    Invia AlertEvent via Telegram e/o Webhook in base alla configurazione env.
    Ogni errore di rete viene catturato e loggato senza interrompere la pipeline.

    Args:
        telegram_enabled: Abilita invio Telegram.
        telegram_bot_token: Token del bot Telegram.
        telegram_chat_id: Chat ID destinazione.
        webhook_enabled: Abilita invio webhook.
        webhook_url: URL endpoint webhook.
        min_level: Livello minimo per inviare notifiche.
        http_timeout: Timeout HTTP in secondi.
    """

    def __init__(
        self,
        telegram_enabled: bool = TELEGRAM_ENABLED,
        telegram_bot_token: str = TELEGRAM_BOT_TOKEN,
        telegram_chat_id: str = TELEGRAM_CHAT_ID,
        webhook_enabled: bool = WEBHOOK_ENABLED,
        webhook_url: str = WEBHOOK_URL,
        min_level: str = NOTIFY_MIN_LEVEL,
        http_timeout: float = _HTTP_TIMEOUT,
    ) -> None:
        self._telegram_enabled = telegram_enabled
        self._telegram_bot_token = telegram_bot_token
        self._telegram_chat_id = telegram_chat_id
        self._webhook_enabled = webhook_enabled
        self._webhook_url = webhook_url
        self._min_level = min_level
        self._http_timeout = http_timeout
        self._client: httpx.AsyncClient | None = None
        self._sent_count: int = 0

        # Validazione configurazione al boot
        if self._telegram_enabled and (
            not self._telegram_bot_token or not self._telegram_chat_id
        ):
            logger.error(
                "notifier: Telegram abilitato ma NOTIFY_TELEGRAM_BOT_TOKEN "
                "o NOTIFY_TELEGRAM_CHAT_ID mancante. Disabilito Telegram."
            )
            self._telegram_enabled = False

        if self._webhook_enabled and not self._webhook_url:
            logger.error(
                "notifier: Webhook abilitato ma NOTIFY_WEBHOOK_URL mancante. "
                "Disabilito Webhook."
            )
            self._webhook_enabled = False

    def _should_notify(self, alert_level: str) -> bool:
        """Verifica se il livello dell'alert raggiunge la soglia minima dell'istanza."""
        return _LEVEL_ORDER.get(alert_level, 0) >= _LEVEL_ORDER.get(self._min_level, 2)

    @property
    def is_active(self) -> bool:
        """Indica se almeno un canale di notifica è attivo."""
        return self._telegram_enabled or self._webhook_enabled

    @property
    def sent_count(self) -> int:
        """Numero totale di notifiche inviate con successo."""
        return self._sent_count

    async def start(self) -> None:
        """Inizializza il client HTTP asincrono."""
        if self.is_active:
            self._client = httpx.AsyncClient(timeout=self._http_timeout)
            channels = []
            if self._telegram_enabled:
                channels.append("Telegram")
            if self._webhook_enabled:
                channels.append("Webhook")
            logger.info("notifier: avviato. Canali attivi: %s", ", ".join(channels))

    async def stop(self) -> None:
        """Chiude il client HTTP."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("notifier: fermato. Notifiche inviate: %d", self._sent_count)

    async def notify(self, alert: AlertEvent) -> None:
        """Invia notifica per un AlertEvent se supera il livello minimo.

        Non solleva mai eccezioni: ogni errore viene loggato come warning.

        Args:
            alert: Evento allarme da notificare.
        """
        if not self.is_active or not self._should_notify(alert.level):
            return

        if self._telegram_enabled:
            await self._send_telegram(alert)

        if self._webhook_enabled:
            await self._send_webhook(alert)

    async def _send_telegram(self, alert: AlertEvent) -> None:
        """Invia un messaggio via Telegram Bot API."""
        if not self._client:
            return

        url = f"https://api.telegram.org/bot{self._telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self._telegram_chat_id,
            "text": _format_telegram_message(alert),
        }

        try:
            resp = await self._client.post(url, json=payload)
            if resp.status_code == 200:
                self._sent_count += 1
                logger.info(
                    "notifier: Telegram inviato [%s] %s", alert.level, alert.source
                )
            else:
                logger.warning(
                    "notifier: Telegram HTTP %d - %s", resp.status_code, resp.text[:200]
                )
        except httpx.HTTPError as exc:
            logger.warning("notifier: Telegram errore rete: %s", exc)

    async def _send_webhook(self, alert: AlertEvent) -> None:
        """Invia un POST JSON al webhook generico."""
        if not self._client:
            return

        payload = _build_webhook_payload(alert)

        try:
            resp = await self._client.post(
                self._webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if 200 <= resp.status_code < 300:
                self._sent_count += 1
                logger.info(
                    "notifier: Webhook inviato [%s] %s", alert.level, alert.source
                )
            else:
                logger.warning(
                    "notifier: Webhook HTTP %d - %s", resp.status_code, resp.text[:200]
                )
        except httpx.HTTPError as exc:
            logger.warning("notifier: Webhook errore rete: %s", exc)
