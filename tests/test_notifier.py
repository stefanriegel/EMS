"""Unit tests for TelegramNotifier."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.notifier import (
    ALERT_COMM_FAILURE,
    ALERT_DISCHARGE_LOCKED,
    ALERT_DISCHARGE_RELEASED,
    TelegramNotifier,
)


def _make_notifier(**kwargs) -> TelegramNotifier:
    defaults = {"token": "test_token", "chat_id": "12345"}
    defaults.update(kwargs)
    return TelegramNotifier(**defaults)


class TestTelegramNotifierInit:
    def test_defaults(self):
        n = _make_notifier()
        assert n._token == "test_token"
        assert n._chat_id == "12345"
        assert n._cooldown_s == 300.0
        assert n._last_sent == {}

    def test_custom_cooldown(self):
        n = _make_notifier(cooldown_s=60.0)
        assert n._cooldown_s == 60.0


class TestTelegramNotifierCooldown:
    async def test_first_alert_always_sends(self):
        n = _make_notifier()
        with patch.object(n, "_send", new_callable=AsyncMock) as mock_send:
            await n.send_alert(ALERT_COMM_FAILURE, "test message")
        mock_send.assert_awaited_once_with(ALERT_COMM_FAILURE, "test message")

    async def test_second_alert_within_cooldown_suppressed(self):
        n = _make_notifier(cooldown_s=300.0)
        with patch.object(n, "_send", new_callable=AsyncMock) as mock_send:
            await n.send_alert(ALERT_COMM_FAILURE, "first")
            await n.send_alert(ALERT_COMM_FAILURE, "second")
        mock_send.assert_awaited_once()  # only first

    async def test_different_categories_independent_cooldown(self):
        n = _make_notifier(cooldown_s=300.0)
        with patch.object(n, "_send", new_callable=AsyncMock) as mock_send:
            await n.send_alert(ALERT_COMM_FAILURE, "failure")
            await n.send_alert(ALERT_DISCHARGE_LOCKED, "locked")
        assert mock_send.await_count == 2  # both sent

    async def test_alert_after_cooldown_sends(self):
        n = _make_notifier(cooldown_s=0.01)  # 10ms cooldown
        with patch.object(n, "_send", new_callable=AsyncMock) as mock_send:
            await n.send_alert(ALERT_COMM_FAILURE, "first")
            time.sleep(0.02)  # wait > cooldown
            await n.send_alert(ALERT_COMM_FAILURE, "second")
        assert mock_send.await_count == 2

    async def test_last_sent_updated_after_send(self):
        n = _make_notifier()
        with patch.object(n, "_send", new_callable=AsyncMock):
            before = time.monotonic()
            await n.send_alert(ALERT_COMM_FAILURE, "msg")
            after = time.monotonic()
        assert ALERT_COMM_FAILURE in n._last_sent
        assert before <= n._last_sent[ALERT_COMM_FAILURE] <= after


class TestTelegramNotifierSend:
    async def test_send_posts_to_correct_url(self):
        n = _make_notifier(token="mytoken")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("backend.notifier.httpx.AsyncClient", return_value=mock_client):
            await n._send(ALERT_COMM_FAILURE, "test")

        mock_client.post.assert_awaited_once()
        url = mock_client.post.call_args[0][0]
        assert "mytoken" in url
        assert "sendMessage" in url

    async def test_send_payload_contains_chat_id_and_text(self):
        n = _make_notifier(chat_id="99999")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("backend.notifier.httpx.AsyncClient", return_value=mock_client):
            await n._send(ALERT_COMM_FAILURE, "hello")

        kwargs = mock_client.post.call_args[1]
        assert kwargs["json"]["chat_id"] == "99999"
        assert kwargs["json"]["text"] == "hello"
        assert kwargs["json"]["parse_mode"] == "HTML"

    async def test_send_logs_error_on_http_error(self):
        n = _make_notifier()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.HTTPError("connection failed"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("backend.notifier.httpx.AsyncClient", return_value=mock_client):
            # Should not raise — errors are caught and logged
            await n._send(ALERT_COMM_FAILURE, "test")


class TestAlertConstants:
    def test_constants_are_distinct_strings(self):
        assert ALERT_COMM_FAILURE != ALERT_DISCHARGE_LOCKED
        assert ALERT_DISCHARGE_LOCKED != ALERT_DISCHARGE_RELEASED
        assert ALERT_COMM_FAILURE != ALERT_DISCHARGE_RELEASED

    def test_constants_are_strings(self):
        assert isinstance(ALERT_COMM_FAILURE, str)
        assert isinstance(ALERT_DISCHARGE_LOCKED, str)
        assert isinstance(ALERT_DISCHARGE_RELEASED, str)
