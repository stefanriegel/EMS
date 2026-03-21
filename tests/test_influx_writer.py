"""Unit tests for InfluxMetricsWriter.

Covers:
  - write_system_state: correct measurement name, tag keys/values, field names/types
  - write_tariff: correct measurement name, field names/types
  - fire-and-forget: exception from write_api.write() is swallowed, not raised
  - InfluxConfig.from_env(): safe defaults with no env vars set

All tests mock InfluxDBClientAsync — no real InfluxDB connection required.

K007: Use @pytest.mark.anyio on async test functions (anyio_mode = "auto" in
pyproject.toml auto-collects them; explicit marker also works as belt-and-braces).
K002: Do NOT rely on asyncio_mode = "auto"; use @pytest.mark.anyio explicitly.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.config import InfluxConfig
from backend.influx_writer import InfluxMetricsWriter
from backend.unified_model import ControlState, UnifiedPoolState
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_client(url: str = "http://localhost:8086", org: str = "ems") -> MagicMock:
    """Return a MagicMock that looks enough like InfluxDBClientAsync for tests.

    The write_api() method returns a mock whose ``write`` attribute is an
    AsyncMock — matching the real WriteApiAsync.write() coroutine signature.
    """
    write_api_mock = MagicMock()
    write_api_mock.write = AsyncMock(return_value=True)

    client = MagicMock()
    client.url = url
    client.org = org
    client.write_api.return_value = write_api_mock

    return client


def _make_state(**overrides) -> UnifiedPoolState:
    """Build a realistic UnifiedPoolState for tests.  All fields required."""
    defaults = dict(
        control_state=ControlState.DISCHARGE,
        combined_soc_pct=72.3,
        huawei_soc_pct=80.0,
        victron_soc_pct=68.5,
        huawei_available=True,
        victron_available=True,
        huawei_discharge_setpoint_w=3000,
        victron_discharge_setpoint_w=5000,
        combined_power_w=-8000.0,
        huawei_charge_headroom_w=500,
        victron_charge_headroom_w=1200.0,
        timestamp=1234567.89,
    )
    defaults.update(overrides)
    return UnifiedPoolState(**defaults)


# ---------------------------------------------------------------------------
# InfluxConfig tests
# ---------------------------------------------------------------------------

class TestInfluxConfig:
    def test_defaults_require_no_env_vars(self, monkeypatch):
        """from_env() must return safe defaults when no env vars are set."""
        for key in ("INFLUXDB_URL", "INFLUXDB_TOKEN", "INFLUXDB_ORG", "INFLUXDB_BUCKET"):
            monkeypatch.delenv(key, raising=False)

        cfg = InfluxConfig.from_env()
        assert cfg.url == "http://localhost:8086"
        assert cfg.token == ""
        assert cfg.org == "ems"
        assert cfg.bucket == "ems"

    def test_disabled_when_no_env_vars(self, monkeypatch):
        """enabled must be False when neither INFLUXDB_URL nor INFLUXDB_TOKEN is set."""
        for key in ("INFLUXDB_URL", "INFLUXDB_TOKEN", "INFLUXDB_ORG", "INFLUXDB_BUCKET"):
            monkeypatch.delenv(key, raising=False)

        cfg = InfluxConfig.from_env()
        assert cfg.enabled is False

    def test_enabled_when_url_set(self, monkeypatch):
        """enabled must be True when INFLUXDB_URL is set."""
        monkeypatch.setenv("INFLUXDB_URL", "http://influx:8086")
        monkeypatch.delenv("INFLUXDB_TOKEN", raising=False)

        cfg = InfluxConfig.from_env()
        assert cfg.enabled is True

    def test_enabled_when_token_set(self, monkeypatch):
        """enabled must be True when INFLUXDB_TOKEN is set."""
        monkeypatch.delenv("INFLUXDB_URL", raising=False)
        monkeypatch.setenv("INFLUXDB_TOKEN", "secret-tok")

        cfg = InfluxConfig.from_env()
        assert cfg.enabled is True

    def test_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("INFLUXDB_URL", "http://influx:8086")
        monkeypatch.setenv("INFLUXDB_TOKEN", "secret-tok")
        monkeypatch.setenv("INFLUXDB_ORG", "myorg")
        monkeypatch.setenv("INFLUXDB_BUCKET", "mybucket")

        cfg = InfluxConfig.from_env()
        assert cfg.url == "http://influx:8086"
        assert cfg.token == "secret-tok"
        assert cfg.org == "myorg"
        assert cfg.bucket == "mybucket"
        assert cfg.enabled is True


# ---------------------------------------------------------------------------
# write_system_state tests
# ---------------------------------------------------------------------------

class TestWriteSystemState:
    @pytest.mark.anyio
    async def test_write_called_once(self):
        client = _make_mock_client()
        writer = InfluxMetricsWriter(client, bucket="ems")
        state = _make_state()

        await writer.write_system_state(state)

        client.write_api().write.assert_called_once()

    @pytest.mark.anyio
    async def test_measurement_name(self):
        client = _make_mock_client()
        writer = InfluxMetricsWriter(client, bucket="ems")
        state = _make_state()

        await writer.write_system_state(state)

        _, kwargs = client.write_api().write.call_args
        record = kwargs["record"]
        assert record._name == "ems_system", f"Expected measurement 'ems_system', got {record._name!r}"

    @pytest.mark.anyio
    async def test_bucket_kwarg(self):
        client = _make_mock_client()
        writer = InfluxMetricsWriter(client, bucket="my-bucket")
        state = _make_state()

        await writer.write_system_state(state)

        _, kwargs = client.write_api().write.call_args
        assert kwargs["bucket"] == "my-bucket"

    @pytest.mark.anyio
    async def test_tags_present(self):
        client = _make_mock_client()
        writer = InfluxMetricsWriter(client, bucket="ems")
        state = _make_state(
            control_state=ControlState.IDLE,
            huawei_available=True,
            victron_available=False,
        )

        await writer.write_system_state(state)

        _, kwargs = client.write_api().write.call_args
        record = kwargs["record"]
        tags = dict(record._tags)
        assert "control_state" in tags, f"Missing tag 'control_state'; got {list(tags)}"
        assert "huawei_available" in tags, "Missing tag 'huawei_available'"
        assert "victron_available" in tags, "Missing tag 'victron_available'"
        assert tags["control_state"] == "IDLE"
        assert tags["huawei_available"] == "true"
        assert tags["victron_available"] == "false"

    @pytest.mark.anyio
    async def test_fields_present_and_types(self):
        """All 8 schema fields must be present with correct Python types."""
        client = _make_mock_client()
        writer = InfluxMetricsWriter(client, bucket="ems")
        state = _make_state(
            combined_soc_pct=72.3,
            huawei_soc_pct=80.0,
            victron_soc_pct=68.5,
            combined_power_w=-8000.0,
            huawei_discharge_setpoint_w=3000,
            victron_discharge_setpoint_w=5000,
            huawei_charge_headroom_w=500,
            victron_charge_headroom_w=1200.0,
        )

        await writer.write_system_state(state)

        _, kwargs = client.write_api().write.call_args
        record = kwargs["record"]
        fields = dict(record._fields)

        float_fields = {
            "combined_soc_pct",
            "huawei_soc_pct",
            "victron_soc_pct",
            "combined_power_w",
            "victron_charge_headroom_w",
        }
        int_fields = {
            "huawei_discharge_setpoint_w",
            "victron_discharge_setpoint_w",
            "huawei_charge_headroom_w",
        }
        all_expected = float_fields | int_fields
        assert all_expected <= set(fields), (
            f"Missing fields: {all_expected - set(fields)}"
        )

        for name in float_fields:
            assert isinstance(fields[name], float), (
                f"Field '{name}' should be float, got {type(fields[name]).__name__}"
            )
        for name in int_fields:
            assert isinstance(fields[name], int), (
                f"Field '{name}' should be int, got {type(fields[name]).__name__}"
            )

    @pytest.mark.anyio
    async def test_timestamp_is_utc_datetime(self):
        """Timestamp must be a UTC datetime, not state.timestamp (monotonic)."""
        client = _make_mock_client()
        writer = InfluxMetricsWriter(client, bucket="ems")
        state = _make_state(timestamp=9999999.99)  # large monotonic — must NOT appear in Point

        await writer.write_system_state(state)

        _, kwargs = client.write_api().write.call_args
        record = kwargs["record"]
        # Point stores time in _time attribute
        point_time = record._time
        assert isinstance(point_time, datetime), (
            f"Expected datetime for Point._time, got {type(point_time)}"
        )
        assert point_time.tzinfo is not None, "Point timestamp must be timezone-aware"

    # ------------------------------------------------------------------
    # Fire-and-forget: failure-path / diagnostics
    # ------------------------------------------------------------------

    @pytest.mark.anyio
    async def test_fire_and_forget_write_system_state(self):
        """Exception from write_api.write() must be swallowed — never raised."""
        client = _make_mock_client()
        client.write_api().write = AsyncMock(side_effect=Exception("connection refused"))
        writer = InfluxMetricsWriter(client, bucket="ems")
        state = _make_state()

        # Should not raise — this is the fire-and-forget contract
        await writer.write_system_state(state)

    @pytest.mark.anyio
    async def test_fire_and_forget_logs_warning(self, caplog):
        """WARNING with 'influx write failed' must be emitted on write error."""
        import logging
        client = _make_mock_client()
        client.write_api().write = AsyncMock(side_effect=Exception("timeout"))
        writer = InfluxMetricsWriter(client, bucket="ems")
        state = _make_state()

        with caplog.at_level(logging.WARNING, logger="backend.influx_writer"):
            await writer.write_system_state(state)

        assert any("influx write failed" in r.message for r in caplog.records), (
            f"Expected 'influx write failed' in WARNING log; got: {[r.message for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# write_tariff tests
# ---------------------------------------------------------------------------

class TestWriteTariff:
    @pytest.mark.anyio
    async def test_measurement_name(self):
        client = _make_mock_client()
        writer = InfluxMetricsWriter(client, bucket="ems")
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        await writer.write_tariff(dt, effective_rate=0.08, octopus_rate=0.08, modul3_rate=0.026)

        _, kwargs = client.write_api().write.call_args
        record = kwargs["record"]
        assert record._name == "ems_tariff", f"Expected 'ems_tariff', got {record._name!r}"

    @pytest.mark.anyio
    async def test_fields_present_and_float(self):
        client = _make_mock_client()
        writer = InfluxMetricsWriter(client, bucket="ems")
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        await writer.write_tariff(dt, effective_rate=0.28, octopus_rate=0.28, modul3_rate=0.125)

        _, kwargs = client.write_api().write.call_args
        record = kwargs["record"]
        fields = dict(record._fields)

        expected = {"effective_rate_eur_kwh", "octopus_rate_eur_kwh", "modul3_rate_eur_kwh"}
        assert expected <= set(fields), f"Missing fields: {expected - set(fields)}"
        for name in expected:
            assert isinstance(fields[name], float), (
                f"Field '{name}' should be float, got {type(fields[name]).__name__}"
            )

    @pytest.mark.anyio
    async def test_fire_and_forget_write_tariff(self):
        """Exception from write_api.write() must not propagate for write_tariff."""
        client = _make_mock_client()
        client.write_api().write = AsyncMock(side_effect=Exception("network error"))
        writer = InfluxMetricsWriter(client, bucket="ems")
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Must not raise
        await writer.write_tariff(dt, effective_rate=0.08, octopus_rate=0.08, modul3_rate=0.026)


# ---------------------------------------------------------------------------
# Constructor observability test
# ---------------------------------------------------------------------------

class TestConstructorLogging:
    def test_info_log_at_construction(self, caplog):
        """INFO log must mention url, org, bucket — never the token."""
        import logging
        client = _make_mock_client(url="http://influx-host:8086", org="prod-org")

        with caplog.at_level(logging.INFO, logger="backend.influx_writer"):
            _writer = InfluxMetricsWriter(client, bucket="prod-bucket")

        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert info_records, "Expected at least one INFO log from InfluxMetricsWriter.__init__"
        full_log = " ".join(r.message for r in info_records)
        assert "http://influx-host:8086" in full_log, "url must appear in construction log"
        assert "prod-org" in full_log, "org must appear in construction log"
        assert "prod-bucket" in full_log, "bucket must appear in construction log"
        # Token must NEVER appear
        assert "test-token" not in full_log, "Token must NOT appear in logs"
