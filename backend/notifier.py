"""Telegram alert notifier for EMS events.

Sends alert messages to a Telegram chat via the Bot API using ``httpx``.
Per-category cooldown suppresses duplicate alerts within a configurable
window (default 5 minutes).

Logging
-------
Module logger: ``backend.notifier``.  Key lines:

* ``INFO  "Telegram alert sent: [{category}] {message}"``   — on success
* ``WARNING "Telegram alert suppressed (cooldown): [{category}]"`` — suppressed
* ``ERROR "Telegram send failed: {exc}"``                   — on HTTP error
"""
from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Alert category constants — used as cooldown keys
ALERT_COMM_FAILURE = "comm_failure"
ALERT_DISCHARGE_LOCKED = "discharge_locked"
ALERT_DISCHARGE_RELEASED = "discharge_released"

# Anomaly detection categories
ALERT_ANOMALY_COMM = "anomaly_comm_loss"
ALERT_ANOMALY_CONSUMPTION = "anomaly_consumption_spike"
ALERT_ANOMALY_SOC = "anomaly_soc_curve"
ALERT_ANOMALY_EFFICIENCY = "anomaly_efficiency"

# Cross-charge detection category
ALERT_CROSS_CHARGE = "cross_charge"


class TelegramNotifier:
    """Sends alert messages to a Telegram chat with per-category cooldown.

    Parameters
    ----------
    token:
        Telegram Bot API token (from BotFather).
    chat_id:
        Target chat or channel ID.
    cooldown_s:
        Minimum seconds between alerts in the same category (default 300).
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        cooldown_s: float = 300.0,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._cooldown_s = cooldown_s
        self._last_sent: dict[str, float] = {}

    async def send_alert(self, category: str, message: str) -> None:
        """Send an alert if the per-category cooldown has elapsed.

        Parameters
        ----------
        category:
            One of the ``ALERT_*`` constants.  Used as cooldown key.
        message:
            Human-readable alert text.  May include HTML tags.
        """
        now = time.monotonic()
        last = self._last_sent.get(category, 0.0)
        if now - last < self._cooldown_s:
            logger.warning("Telegram alert suppressed (cooldown): [%s]", category)
            return
        await self._send(category, message)
        self._last_sent[category] = now

    async def _send(self, category: str, message: str) -> None:
        url = _TELEGRAM_API.format(token=self._token)
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=10.0)
                response.raise_for_status()
            logger.info("Telegram alert sent: [%s] %s", category, message)
        except httpx.HTTPError as exc:
            logger.error("Telegram send failed: %s", exc)
