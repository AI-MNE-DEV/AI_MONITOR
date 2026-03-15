"""
Test suite per il modulo notifier.py.

Testa il dispatcher di notifiche esterne (Telegram + Webhook)
con mock HTTP per evitare chiamate reali.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from contracts import AlertEvent
from notifier import (
    Notifier,
    _build_webhook_payload,
    _format_telegram_message,
    _should_notify,
)


# --- Fixtures ---


def _make_alert(level: str = "CRITICAL", source: str = "host") -> AlertEvent:
    """Crea un AlertEvent di test."""
    return AlertEvent(
        id="test123",
        timestamp=datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc),
        level=level,
        source=source,
        message="CPU al 97% (soglia critica: 95%)",
        metric_value=97.0,
    )


# --- Unit: funzioni helper ---


class TestShouldNotify:
    def test_critical_alert_with_critical_threshold(self):
        assert _should_notify("CRITICAL") is True

    def test_warning_alert_with_critical_threshold(self):
        """WARNING non raggiunge soglia CRITICAL di default."""
        assert _should_notify("WARNING") is False

    def test_info_alert_below_threshold(self):
        assert _should_notify("INFO") is False


class TestFormatTelegramMessage:
    def test_critical_has_red_icon(self):
        alert = _make_alert(level="CRITICAL")
        msg = _format_telegram_message(alert)
        assert "🔴" in msg
        assert "CRITICAL" in msg
        assert "97.0%" in msg

    def test_warning_has_yellow_icon(self):
        alert = _make_alert(level="WARNING")
        msg = _format_telegram_message(alert)
        assert "🟡" in msg

    def test_none_metric_shows_na(self):
        alert = _make_alert()
        alert = alert.model_copy(update={"metric_value": None})
        msg = _format_telegram_message(alert)
        assert "N/A" in msg


class TestBuildWebhookPayload:
    def test_payload_structure(self):
        alert = _make_alert()
        payload = _build_webhook_payload(alert)
        assert payload["event"] == "ai_monitor_alert"
        assert payload["id"] == "test123"
        assert payload["level"] == "CRITICAL"
        assert payload["source"] == "host"
        assert payload["metric_value"] == 97.0
        assert "sent_at" in payload


# --- Unit: classe Notifier ---


class TestNotifierInit:
    def test_disabled_by_default(self):
        n = Notifier()
        assert n.is_active is False
        assert n.sent_count == 0

    def test_telegram_enabled_without_token_disables(self):
        n = Notifier(
            telegram_enabled=True, telegram_bot_token="", telegram_chat_id="123"
        )
        assert n._telegram_enabled is False

    def test_telegram_enabled_without_chat_id_disables(self):
        n = Notifier(
            telegram_enabled=True, telegram_bot_token="tok", telegram_chat_id=""
        )
        assert n._telegram_enabled is False

    def test_webhook_enabled_without_url_disables(self):
        n = Notifier(webhook_enabled=True, webhook_url="")
        assert n._webhook_enabled is False

    def test_valid_telegram_config(self):
        n = Notifier(
            telegram_enabled=True,
            telegram_bot_token="123:ABC",
            telegram_chat_id="-100123",
        )
        assert n._telegram_enabled is True
        assert n.is_active is True

    def test_valid_webhook_config(self):
        n = Notifier(webhook_enabled=True, webhook_url="https://hooks.example.com/x")
        assert n._webhook_enabled is True
        assert n.is_active is True


# --- Async: start/stop lifecycle ---


@pytest.mark.asyncio
async def test_start_creates_client():
    n = Notifier(webhook_enabled=True, webhook_url="https://example.com")
    await n.start()
    assert n._client is not None
    await n.stop()
    assert n._client is None


@pytest.mark.asyncio
async def test_start_inactive_no_client():
    n = Notifier()
    await n.start()
    assert n._client is None


# --- Async: notify con mock HTTP ---


@pytest.mark.asyncio
async def test_notify_telegram_success():
    n = Notifier(
        telegram_enabled=True,
        telegram_bot_token="123:ABC",
        telegram_chat_id="-100123",
    )
    await n.start()

    mock_response = httpx.Response(200, json={"ok": True})
    n._client.post = AsyncMock(return_value=mock_response)  # type: ignore[union-attr]

    alert = _make_alert()
    await n.notify(alert)

    assert n.sent_count == 1
    n._client.post.assert_called_once()  # type: ignore[union-attr]
    call_args = n._client.post.call_args  # type: ignore[union-attr]
    assert "api.telegram.org" in call_args[0][0]

    await n.stop()


@pytest.mark.asyncio
async def test_notify_webhook_success():
    n = Notifier(webhook_enabled=True, webhook_url="https://hooks.example.com/alert")
    await n.start()

    mock_response = httpx.Response(200)
    n._client.post = AsyncMock(return_value=mock_response)  # type: ignore[union-attr]

    alert = _make_alert()
    await n.notify(alert)

    assert n.sent_count == 1
    call_args = n._client.post.call_args  # type: ignore[union-attr]
    assert call_args[0][0] == "https://hooks.example.com/alert"

    await n.stop()


@pytest.mark.asyncio
async def test_notify_both_channels():
    n = Notifier(
        telegram_enabled=True,
        telegram_bot_token="123:ABC",
        telegram_chat_id="-100123",
        webhook_enabled=True,
        webhook_url="https://hooks.example.com/alert",
    )
    await n.start()

    mock_response = httpx.Response(200, json={"ok": True})
    n._client.post = AsyncMock(return_value=mock_response)  # type: ignore[union-attr]

    await n.notify(_make_alert())

    assert n.sent_count == 2  # 1 Telegram + 1 Webhook
    assert n._client.post.call_count == 2  # type: ignore[union-attr]

    await n.stop()


@pytest.mark.asyncio
async def test_notify_skips_below_min_level():
    n = Notifier(
        telegram_enabled=True,
        telegram_bot_token="123:ABC",
        telegram_chat_id="-100123",
        min_level="CRITICAL",
    )
    await n.start()
    n._client.post = AsyncMock()  # type: ignore[union-attr]

    await n.notify(_make_alert(level="WARNING"))

    assert n.sent_count == 0
    n._client.post.assert_not_called()  # type: ignore[union-attr]

    await n.stop()


@pytest.mark.asyncio
async def test_notify_warning_level_when_min_is_warning():
    n = Notifier(
        telegram_enabled=True,
        telegram_bot_token="123:ABC",
        telegram_chat_id="-100123",
        min_level="WARNING",
    )
    await n.start()

    mock_response = httpx.Response(200, json={"ok": True})
    n._client.post = AsyncMock(return_value=mock_response)  # type: ignore[union-attr]

    await n.notify(_make_alert(level="WARNING"))

    assert n.sent_count == 1

    await n.stop()


@pytest.mark.asyncio
async def test_telegram_http_error_does_not_raise():
    """Errore di rete non deve mai crashare la pipeline."""
    n = Notifier(
        telegram_enabled=True,
        telegram_bot_token="123:ABC",
        telegram_chat_id="-100123",
    )
    await n.start()

    n._client.post = AsyncMock(side_effect=httpx.ConnectError("timeout"))  # type: ignore[union-attr]

    await n.notify(_make_alert())  # Non deve sollevare eccezioni

    assert n.sent_count == 0

    await n.stop()


@pytest.mark.asyncio
async def test_webhook_non_200_logs_warning():
    n = Notifier(webhook_enabled=True, webhook_url="https://hooks.example.com/alert")
    await n.start()

    mock_response = httpx.Response(500, text="Internal Server Error")
    n._client.post = AsyncMock(return_value=mock_response)  # type: ignore[union-attr]

    await n.notify(_make_alert())

    assert n.sent_count == 0

    await n.stop()


@pytest.mark.asyncio
async def test_notify_inactive_notifier_is_noop():
    n = Notifier()
    await n.start()
    await n.notify(_make_alert())
    assert n.sent_count == 0
