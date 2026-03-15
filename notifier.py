"""
Notifier - Dispatcher asincrono per notifiche esterne.

Invia alert critici via Telegram Bot API, Webhook generico (POST JSON) e/o Email SMTP.
Configurazione interamente via variabili d'ambiente. Non blocca mai la pipeline:
in caso di errore di rete logga un warning e prosegue.
"""

import asyncio
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

import httpx

from contracts import AlertEvent

logger = logging.getLogger(__name__)

# --- Configurazione via env ---
TELEGRAM_ENABLED: bool = os.getenv("NOTIFY_TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN: str = os.getenv("NOTIFY_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("NOTIFY_TELEGRAM_CHAT_ID", "")

WEBHOOK_ENABLED: bool = os.getenv("NOTIFY_WEBHOOK_ENABLED", "false").lower() == "true"
WEBHOOK_URL: str = os.getenv("NOTIFY_WEBHOOK_URL", "")

EMAIL_ENABLED: bool = os.getenv("NOTIFY_EMAIL_ENABLED", "false").lower() == "true"
EMAIL_SMTP_HOST: str = os.getenv("NOTIFY_EMAIL_SMTP_HOST", "")
EMAIL_SMTP_PORT: int = int(os.getenv("NOTIFY_EMAIL_SMTP_PORT", "587"))
EMAIL_SMTP_USER: str = os.getenv("NOTIFY_EMAIL_SMTP_USER", "")
EMAIL_SMTP_PASS: str = os.getenv("NOTIFY_EMAIL_SMTP_PASS", "")
EMAIL_FROM: str = os.getenv("NOTIFY_EMAIL_FROM", "")
EMAIL_TO: str = os.getenv("NOTIFY_EMAIL_TO", "")
EMAIL_USE_TLS: bool = os.getenv("NOTIFY_EMAIL_USE_TLS", "true").lower() == "true"

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


def _build_email_message(
    alert: AlertEvent, from_addr: str, to_addr: str
) -> EmailMessage:
    """Costruisce un EmailMessage per l'alert."""
    msg = EmailMessage()
    icon = "[CRITICAL]" if alert.level == "CRITICAL" else "[WARNING]"
    msg["Subject"] = f"{icon} AI Monitor: {alert.source} - {alert.message[:60]}"
    msg["From"] = from_addr
    msg["To"] = to_addr
    value_str = (
        f"{alert.metric_value:.1f}%" if alert.metric_value is not None else "N/A"
    )
    msg.set_content(
        f"AI MONITOR ALERT\n"
        f"{'=' * 40}\n"
        f"Level:   {alert.level}\n"
        f"Source:  {alert.source}\n"
        f"Message: {alert.message}\n"
        f"Value:   {value_str}\n"
        f"Time:    {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"ID:      {alert.id}\n"
    )
    return msg


class Notifier:
    """Dispatcher di notifiche esterne non-bloccante.

    Invia AlertEvent via Telegram, Webhook e/o Email SMTP.
    Ogni errore di rete viene catturato e loggato senza interrompere la pipeline.
    """

    def __init__(
        self,
        telegram_enabled: bool = TELEGRAM_ENABLED,
        telegram_bot_token: str = TELEGRAM_BOT_TOKEN,
        telegram_chat_id: str = TELEGRAM_CHAT_ID,
        webhook_enabled: bool = WEBHOOK_ENABLED,
        webhook_url: str = WEBHOOK_URL,
        email_enabled: bool = EMAIL_ENABLED,
        email_smtp_host: str = EMAIL_SMTP_HOST,
        email_smtp_port: int = EMAIL_SMTP_PORT,
        email_smtp_user: str = EMAIL_SMTP_USER,
        email_smtp_pass: str = EMAIL_SMTP_PASS,
        email_from: str = EMAIL_FROM,
        email_to: str = EMAIL_TO,
        email_use_tls: bool = EMAIL_USE_TLS,
        min_level: str = NOTIFY_MIN_LEVEL,
        http_timeout: float = _HTTP_TIMEOUT,
    ) -> None:
        self._telegram_enabled = telegram_enabled
        self._telegram_bot_token = telegram_bot_token
        self._telegram_chat_id = telegram_chat_id
        self._webhook_enabled = webhook_enabled
        self._webhook_url = webhook_url
        self._email_enabled = email_enabled
        self._email_smtp_host = email_smtp_host
        self._email_smtp_port = email_smtp_port
        self._email_smtp_user = email_smtp_user
        self._email_smtp_pass = email_smtp_pass
        self._email_from = email_from
        self._email_to = email_to
        self._email_use_tls = email_use_tls
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

        if self._email_enabled and (
            not self._email_smtp_host or not self._email_from or not self._email_to
        ):
            logger.error(
                "notifier: Email abilitata ma SMTP_HOST/FROM/TO mancanti. "
                "Disabilito Email."
            )
            self._email_enabled = False

    def _should_notify(self, alert_level: str) -> bool:
        """Verifica se il livello dell'alert raggiunge la soglia minima dell'istanza."""
        return _LEVEL_ORDER.get(alert_level, 0) >= _LEVEL_ORDER.get(self._min_level, 2)

    @property
    def is_active(self) -> bool:
        """Indica se almeno un canale di notifica è attivo."""
        return self._telegram_enabled or self._webhook_enabled or self._email_enabled

    @property
    def sent_count(self) -> int:
        """Numero totale di notifiche inviate con successo."""
        return self._sent_count

    async def start(self) -> None:
        """Inizializza il client HTTP asincrono."""
        if self._telegram_enabled or self._webhook_enabled:
            self._client = httpx.AsyncClient(timeout=self._http_timeout)
        if self.is_active:
            channels = []
            if self._telegram_enabled:
                channels.append("Telegram")
            if self._webhook_enabled:
                channels.append("Webhook")
            if self._email_enabled:
                channels.append(f"Email({self._email_smtp_host})")
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

        if self._email_enabled:
            await self._send_email(alert)

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

    def _send_email_sync(self, alert: AlertEvent) -> None:
        """Invio SMTP sincrono (eseguito via asyncio.to_thread)."""
        msg = _build_email_message(alert, self._email_from, self._email_to)
        with smtplib.SMTP(
            self._email_smtp_host, self._email_smtp_port, timeout=10
        ) as server:
            if self._email_use_tls:
                server.starttls()
            if self._email_smtp_user and self._email_smtp_pass:
                server.login(self._email_smtp_user, self._email_smtp_pass)
            server.send_message(msg)

    async def _send_email(self, alert: AlertEvent) -> None:
        """Invia un'email alert via SMTP in un thread separato."""
        try:
            await asyncio.to_thread(self._send_email_sync, alert)
            self._sent_count += 1
            logger.info("notifier: Email inviata [%s] %s", alert.level, alert.source)
        except Exception as exc:
            logger.warning("notifier: Email errore SMTP: %s", exc)
