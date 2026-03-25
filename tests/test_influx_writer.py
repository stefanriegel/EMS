"""Unit tests for InfluxMetricsWriter (v1 line protocol).

Covers:
  - _LineProtocolBuilder: correct line protocol generation
  - write_system_state: correct measurement name, tags, fields, timestamp
  - write_tariff: correct measurement name, fields
  - write_per_system_metrics: two points in one call
  - write_decision: trigger as tag, roles as string fields
  - write_coordinator_state: ems_system with role/pool tags
  - fire-and-forget: exception from HTTP POST is swallowed, not raised
  - InfluxConfig.from_env(): safe defaults, backward compat

All tests mock httpx.AsyncClient -- no real InfluxDB connection required.

K007: Use @pytest.mark.anyio on async test functions.
K002: Do NOT rely on asyncio_mode = "auto"; use @pytest.mark.anyio explicitly.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.config import InfluxConfig
from backend.controller_model import (
    BatteryRole,
    ControllerSnapshot,
    CoordinatorState,
    DecisionEntry,
    IntegrationStatus,
)
from backend.influx_writer import InfluxMetricsWriter, _LineProtocolBuilder
from backend.unified_model import ControlState, UnifiedPoolState
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_writer() -> tuple[InfluxMetricsWriter, AsyncMock]:
    """Return an InfluxMetricsWriter with a mocked httpx client.

    Returns (writer, mock_post) where mock_post is the AsyncMock for
    the HTTP POST method.
    """
    writer = InfluxMetricsWriter(
        url="http://localhost:8086",
        database="ems",
        username="",
        password="",
    )
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_post = AsyncMock(return_value=mock_response)
    writer._http = MagicMock()
    writer._http.post = mock_post
    writer._http.aclose = AsyncMock()
    return writer, mock_post


def _parse_line(line: str) -> dict:
    """Parse a single line protocol string into components for assertions.

    Returns dict with keys: measurement, tags (dict), fields (dict), timestamp (str|None).
    """
    # Split: measurement[,tags] fields [timestamp]
    parts = line.split(" ")
    measurement_tags = parts[0]
    fields_str = parts[1]
    timestamp = parts[2] if len(parts) > 2 else None

    if "," in measurement_tags:
        measurement, tags_str = measurement_tags.split(",", 1)
        tags = dict(kv.split("=", 1) for kv in tags_str.split(","))
    else:
        measurement = measurement_tags
        tags = {}

    fields = {}
    for kv in fields_str.split(","):
        k, v = kv.split("=", 1)
        fields[k] = v

    return {
        "measurement": measurement,
        "tags": tags,
        "fields": fields,
        "timestamp": timestamp,
    }


def _get_posted_lines(mock_post: AsyncMock) -> list[str]:
    """Extract the line protocol body from the mock POST call."""
    _, kwargs = mock_post.call_args
    body: str = kwargs["content"]
    return [line for line in body.split("\n") if line.strip()]


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
# _LineProtocolBuilder tests
# ---------------------------------------------------------------------------


class TestLineProtocolBuilder:
    def test_simple_float_field(self):
        line = _LineProtocolBuilder("cpu").field_float("usage", 42.5).to_line()
        assert line == "cpu usage=42.5"

    def test_int_field_has_suffix(self):
        line = _LineProtocolBuilder("mem").field_int("used", 1024).to_line()
        assert line == "mem used=1024i"

    def test_string_field_is_quoted(self):
        line = _LineProtocolBuilder("log").field_str("msg", "hello").to_line()
        assert line == 'log msg="hello"'

    def test_string_field_escapes_quotes(self):
        line = _LineProtocolBuilder("log").field_str("msg", 'say "hi"').to_line()
        assert line == 'log msg="say \\"hi\\""'

    def test_bool_field(self):
        line = _LineProtocolBuilder("status").field_bool("active", True).to_line()
        assert line == "status active=true"
        line2 = _LineProtocolBuilder("status").field_bool("active", False).to_line()
        assert line2 == "status active=false"

    def test_tag(self):
        line = _LineProtocolBuilder("cpu").tag("host", "srv1").field_float("val", 1.0).to_line()
        assert line == "cpu,host=srv1 val=1.0"

    def test_multiple_tags(self):
        line = (
            _LineProtocolBuilder("cpu")
            .tag("host", "srv1")
            .tag("region", "eu")
            .field_float("val", 1.0)
            .to_line()
        )
        assert line == "cpu,host=srv1,region=eu val=1.0"

    def test_timestamp(self):
        dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        line = _LineProtocolBuilder("m").field_float("v", 1.0).time_ns(dt).to_line()
        assert line.endswith(str(int(dt.timestamp() * 1_000_000_000)))

    def test_tag_escaping(self):
        line = (
            _LineProtocolBuilder("m")
            .tag("host", "srv 1")
            .field_float("v", 1.0)
            .to_line()
        )
        assert "host=srv\\ 1" in line


# ---------------------------------------------------------------------------
# InfluxConfig tests
# ---------------------------------------------------------------------------


class TestInfluxConfig:
    def test_defaults_require_no_env_vars(self, monkeypatch):
        """from_env() must return safe defaults when no env vars are set."""
        for key in (
            "INFLUXDB_URL", "INFLUXDB_TOKEN", "INFLUXDB_DATABASE",
            "INFLUXDB_BUCKET", "INFLUXDB_USERNAME", "INFLUXDB_PASSWORD",
        ):
            monkeypatch.delenv(key, raising=False)

        cfg = InfluxConfig.from_env()
        assert cfg.url == "http://localhost:8086"
        assert cfg.database == "ems"
        assert cfg.username == ""
        assert cfg.password == ""

    def test_disabled_when_no_env_vars(self, monkeypatch):
        """enabled must be False when INFLUXDB_URL is not set."""
        for key in (
            "INFLUXDB_URL", "INFLUXDB_TOKEN", "INFLUXDB_DATABASE",
            "INFLUXDB_BUCKET", "INFLUXDB_USERNAME", "INFLUXDB_PASSWORD",
        ):
            monkeypatch.delenv(key, raising=False)

        cfg = InfluxConfig.from_env()
        assert cfg.enabled is False

    def test_enabled_when_url_set(self, monkeypatch):
        """enabled must be True when INFLUXDB_URL is set."""
        monkeypatch.setenv("INFLUXDB_URL", "http://influx:8086")
        for key in ("INFLUXDB_TOKEN", "INFLUXDB_DATABASE", "INFLUXDB_BUCKET",
                     "INFLUXDB_USERNAME", "INFLUXDB_PASSWORD"):
            monkeypatch.delenv(key, raising=False)

        cfg = InfluxConfig.from_env()
        assert cfg.enabled is True

    def test_backward_compat_bucket_as_database(self, monkeypatch):
        """INFLUXDB_BUCKET should be accepted as database name."""
        monkeypatch.setenv("INFLUXDB_URL", "http://influx:8086")
        monkeypatch.setenv("INFLUXDB_BUCKET", "mybucket")
        monkeypatch.delenv("INFLUXDB_DATABASE", raising=False)
        monkeypatch.delenv("INFLUXDB_TOKEN", raising=False)
        monkeypatch.delenv("INFLUXDB_USERNAME", raising=False)
        monkeypatch.delenv("INFLUXDB_PASSWORD", raising=False)

        cfg = InfluxConfig.from_env()
        assert cfg.database == "mybucket"

    def test_database_takes_precedence_over_bucket(self, monkeypatch):
        """INFLUXDB_DATABASE should take precedence over INFLUXDB_BUCKET."""
        monkeypatch.setenv("INFLUXDB_URL", "http://influx:8086")
        monkeypatch.setenv("INFLUXDB_DATABASE", "mydb")
        monkeypatch.setenv("INFLUXDB_BUCKET", "mybucket")
        monkeypatch.delenv("INFLUXDB_TOKEN", raising=False)
        monkeypatch.delenv("INFLUXDB_USERNAME", raising=False)
        monkeypatch.delenv("INFLUXDB_PASSWORD", raising=False)

        cfg = InfluxConfig.from_env()
        assert cfg.database == "mydb"

    def test_backward_compat_token_as_password(self, monkeypatch):
        """INFLUXDB_TOKEN should be accepted as password."""
        monkeypatch.setenv("INFLUXDB_URL", "http://influx:8086")
        monkeypatch.setenv("INFLUXDB_TOKEN", "secret-tok")
        monkeypatch.delenv("INFLUXDB_DATABASE", raising=False)
        monkeypatch.delenv("INFLUXDB_BUCKET", raising=False)
        monkeypatch.delenv("INFLUXDB_USERNAME", raising=False)
        monkeypatch.delenv("INFLUXDB_PASSWORD", raising=False)

        cfg = InfluxConfig.from_env()
        assert cfg.password == "secret-tok"

    def test_password_takes_precedence_over_token(self, monkeypatch):
        """INFLUXDB_PASSWORD should take precedence over INFLUXDB_TOKEN."""
        monkeypatch.setenv("INFLUXDB_URL", "http://influx:8086")
        monkeypatch.setenv("INFLUXDB_PASSWORD", "mypass")
        monkeypatch.setenv("INFLUXDB_TOKEN", "secret-tok")
        monkeypatch.delenv("INFLUXDB_DATABASE", raising=False)
        monkeypatch.delenv("INFLUXDB_BUCKET", raising=False)
        monkeypatch.delenv("INFLUXDB_USERNAME", raising=False)

        cfg = InfluxConfig.from_env()
        assert cfg.password == "mypass"

    def test_reads_all_env_vars(self, monkeypatch):
        monkeypatch.setenv("INFLUXDB_URL", "http://influx:8086")
        monkeypatch.setenv("INFLUXDB_DATABASE", "mydb")
        monkeypatch.setenv("INFLUXDB_USERNAME", "admin")
        monkeypatch.setenv("INFLUXDB_PASSWORD", "secret")
        monkeypatch.delenv("INFLUXDB_TOKEN", raising=False)
        monkeypatch.delenv("INFLUXDB_BUCKET", raising=False)

        cfg = InfluxConfig.from_env()
        assert cfg.url == "http://influx:8086"
        assert cfg.database == "mydb"
        assert cfg.username == "admin"
        assert cfg.password == "secret"
        assert cfg.enabled is True


# ---------------------------------------------------------------------------
# write_system_state tests
# ---------------------------------------------------------------------------


class TestWriteSystemState:
    @pytest.mark.anyio
    async def test_post_called_once(self):
        writer, mock_post = _make_writer()
        state = _make_state()

        await writer.write_system_state(state)

        mock_post.assert_called_once()

    @pytest.mark.anyio
    async def test_measurement_name(self):
        writer, mock_post = _make_writer()
        state = _make_state()

        await writer.write_system_state(state)

        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        assert parsed["measurement"] == "ems_system"

    @pytest.mark.anyio
    async def test_write_url_and_params(self):
        writer, mock_post = _make_writer()
        state = _make_state()

        await writer.write_system_state(state)

        _, kwargs = mock_post.call_args
        assert kwargs["params"]["db"] == "ems"
        assert kwargs["params"]["precision"] == "ns"

    @pytest.mark.anyio
    async def test_tags_present(self):
        writer, mock_post = _make_writer()
        state = _make_state(
            control_state=ControlState.IDLE,
            huawei_available=True,
            victron_available=False,
        )

        await writer.write_system_state(state)

        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        assert parsed["tags"]["control_state"] == "IDLE"
        assert parsed["tags"]["huawei_available"] == "true"
        assert parsed["tags"]["victron_available"] == "false"

    @pytest.mark.anyio
    async def test_fields_present(self):
        """All 8 schema fields must be present."""
        writer, mock_post = _make_writer()
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

        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        expected_fields = {
            "combined_soc_pct",
            "huawei_soc_pct",
            "victron_soc_pct",
            "combined_power_w",
            "huawei_discharge_setpoint_w",
            "victron_discharge_setpoint_w",
            "huawei_charge_headroom_w",
            "victron_charge_headroom_w",
        }
        assert expected_fields <= set(parsed["fields"]), (
            f"Missing fields: {expected_fields - set(parsed['fields'])}"
        )

    @pytest.mark.anyio
    async def test_int_fields_have_i_suffix(self):
        """Integer fields must have 'i' suffix in line protocol."""
        writer, mock_post = _make_writer()
        state = _make_state(
            huawei_discharge_setpoint_w=3000,
            victron_discharge_setpoint_w=5000,
            huawei_charge_headroom_w=500,
        )

        await writer.write_system_state(state)

        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        assert parsed["fields"]["huawei_discharge_setpoint_w"] == "3000i"
        assert parsed["fields"]["victron_discharge_setpoint_w"] == "5000i"
        assert parsed["fields"]["huawei_charge_headroom_w"] == "500i"

    @pytest.mark.anyio
    async def test_has_timestamp(self):
        """Line protocol must include a nanosecond timestamp."""
        writer, mock_post = _make_writer()
        state = _make_state()

        await writer.write_system_state(state)

        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        assert parsed["timestamp"] is not None
        assert int(parsed["timestamp"]) > 0

    # ------------------------------------------------------------------
    # Fire-and-forget
    # ------------------------------------------------------------------

    @pytest.mark.anyio
    async def test_fire_and_forget_write_system_state(self):
        """Exception from HTTP POST must be swallowed -- never raised."""
        writer, mock_post = _make_writer()
        mock_post.side_effect = Exception("connection refused")
        state = _make_state()

        # Should not raise
        await writer.write_system_state(state)

    @pytest.mark.anyio
    async def test_fire_and_forget_logs_warning(self, caplog):
        """WARNING with 'influx write failed' must be emitted on write error."""
        import logging
        writer, mock_post = _make_writer()
        mock_post.side_effect = Exception("timeout")
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
        writer, mock_post = _make_writer()
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        await writer.write_tariff(dt, effective_rate=0.08, octopus_rate=0.08, modul3_rate=0.026)

        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        assert parsed["measurement"] == "ems_tariff"

    @pytest.mark.anyio
    async def test_fields_present(self):
        writer, mock_post = _make_writer()
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        await writer.write_tariff(dt, effective_rate=0.28, octopus_rate=0.28, modul3_rate=0.125)

        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        expected = {"effective_rate_eur_kwh", "octopus_rate_eur_kwh", "modul3_rate_eur_kwh"}
        assert expected <= set(parsed["fields"]), f"Missing fields: {expected - set(parsed['fields'])}"

    @pytest.mark.anyio
    async def test_fire_and_forget_write_tariff(self):
        """Exception from HTTP POST must not propagate for write_tariff."""
        writer, mock_post = _make_writer()
        mock_post.side_effect = Exception("network error")
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Must not raise
        await writer.write_tariff(dt, effective_rate=0.08, octopus_rate=0.08, modul3_rate=0.026)


# ---------------------------------------------------------------------------
# Constructor observability test
# ---------------------------------------------------------------------------


class TestConstructorLogging:
    def test_info_log_at_construction(self, caplog):
        """INFO log must mention url and database -- never the password."""
        import logging

        with caplog.at_level(logging.INFO, logger="backend.influx_writer"):
            _writer = InfluxMetricsWriter(
                url="http://influx-host:8086",
                database="prod-db",
                username="admin",
                password="super-secret",
            )

        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert info_records, "Expected at least one INFO log from InfluxMetricsWriter.__init__"
        full_log = " ".join(r.message for r in info_records)
        assert "http://influx-host:8086" in full_log, "url must appear in construction log"
        assert "prod-db" in full_log, "database must appear in construction log"
        # Password must NEVER appear
        assert "super-secret" not in full_log, "Password must NOT appear in logs"


# ---------------------------------------------------------------------------
# DecisionEntry / IntegrationStatus model tests
# ---------------------------------------------------------------------------


class TestDecisionEntry:
    def test_instantiation(self):
        """DecisionEntry can be instantiated with all required fields."""
        entry = DecisionEntry(
            timestamp="2026-03-22T12:00:00Z",
            trigger="role_change",
            huawei_role="PRIMARY_DISCHARGE",
            victron_role="SECONDARY_DISCHARGE",
            p_target_w=-5000.0,
            huawei_allocation_w=-3000.0,
            victron_allocation_w=-2000.0,
            pool_status="NORMAL",
            reasoning="Huawei higher SoC, assigned primary",
        )
        assert entry.trigger == "role_change"
        assert entry.huawei_role == "PRIMARY_DISCHARGE"
        assert entry.p_target_w == -5000.0
        assert entry.reasoning == "Huawei higher SoC, assigned primary"


class TestIntegrationStatus:
    def test_instantiation(self):
        """IntegrationStatus can be instantiated with all fields."""
        status = IntegrationStatus(
            service="influxdb",
            available=True,
            last_error=None,
            last_seen=datetime(2026, 3, 22, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert status.service == "influxdb"
        assert status.available is True
        assert status.last_error is None

    def test_defaults(self):
        """Optional fields default to None."""
        status = IntegrationStatus(service="evcc", available=False)
        assert status.last_error is None
        assert status.last_seen is None


# ---------------------------------------------------------------------------
# Helpers for new InfluxDB methods
# ---------------------------------------------------------------------------


def _make_huawei_snapshot(**overrides) -> ControllerSnapshot:
    defaults = dict(
        soc_pct=80.0,
        power_w=-3000.0,
        available=True,
        role=BatteryRole.PRIMARY_DISCHARGE,
        consecutive_failures=0,
        timestamp=1234567.89,
        charge_headroom_w=500.0,
    )
    defaults.update(overrides)
    return ControllerSnapshot(**defaults)


def _make_victron_snapshot(**overrides) -> ControllerSnapshot:
    defaults = dict(
        soc_pct=68.5,
        power_w=-2000.0,
        available=True,
        role=BatteryRole.SECONDARY_DISCHARGE,
        consecutive_failures=0,
        timestamp=1234567.89,
        charge_headroom_w=1200.0,
        grid_l1_power_w=100.0,
        grid_l2_power_w=200.0,
        grid_l3_power_w=300.0,
    )
    defaults.update(overrides)
    return ControllerSnapshot(**defaults)


def _make_coordinator_state(**overrides) -> CoordinatorState:
    defaults = dict(
        combined_soc_pct=72.3,
        huawei_soc_pct=80.0,
        victron_soc_pct=68.5,
        huawei_available=True,
        victron_available=True,
        control_state="DISCHARGE",
        huawei_discharge_setpoint_w=3000,
        victron_discharge_setpoint_w=2000,
        combined_power_w=-5000.0,
        huawei_charge_headroom_w=500,
        victron_charge_headroom_w=1200.0,
        timestamp=1234567.89,
        huawei_role="PRIMARY_DISCHARGE",
        victron_role="SECONDARY_DISCHARGE",
        pool_status="NORMAL",
    )
    defaults.update(overrides)
    return CoordinatorState(**defaults)


# ---------------------------------------------------------------------------
# write_per_system_metrics tests
# ---------------------------------------------------------------------------


class TestWritePerSystemMetrics:
    @pytest.mark.anyio
    async def test_writes_two_points(self):
        """write_per_system_metrics must POST two line protocol lines."""
        writer, mock_post = _make_writer()
        h = _make_huawei_snapshot()
        v = _make_victron_snapshot()

        await writer.write_per_system_metrics(h, v, "PRIMARY_DISCHARGE", "SECONDARY_DISCHARGE")

        mock_post.assert_called_once()
        lines = _get_posted_lines(mock_post)
        assert len(lines) == 2
        measurements = [_parse_line(l)["measurement"] for l in lines]
        assert "ems_huawei" in measurements
        assert "ems_victron" in measurements

    @pytest.mark.anyio
    async def test_huawei_point_fields(self):
        """ems_huawei point must have soc_pct, power_w, charge_headroom_w fields."""
        writer, mock_post = _make_writer()
        h = _make_huawei_snapshot(soc_pct=80.0, power_w=-3000.0, charge_headroom_w=500.0)
        v = _make_victron_snapshot()

        await writer.write_per_system_metrics(h, v, "PRIMARY_DISCHARGE", "SECONDARY_DISCHARGE")

        lines = _get_posted_lines(mock_post)
        huawei_line = [l for l in lines if l.startswith("ems_huawei")][0]
        parsed = _parse_line(huawei_line)
        assert "soc_pct" in parsed["fields"]
        assert "power_w" in parsed["fields"]
        assert "charge_headroom_w" in parsed["fields"]

    @pytest.mark.anyio
    async def test_victron_point_has_per_phase_fields(self):
        """ems_victron point must include grid_l1/l2/l3 fields."""
        writer, mock_post = _make_writer()
        h = _make_huawei_snapshot()
        v = _make_victron_snapshot(
            grid_l1_power_w=100.0, grid_l2_power_w=200.0, grid_l3_power_w=300.0,
        )

        await writer.write_per_system_metrics(h, v, "PRIMARY_DISCHARGE", "SECONDARY_DISCHARGE")

        lines = _get_posted_lines(mock_post)
        victron_line = [l for l in lines if l.startswith("ems_victron")][0]
        parsed = _parse_line(victron_line)
        assert "grid_l1_power_w" in parsed["fields"]
        assert "grid_l2_power_w" in parsed["fields"]
        assert "grid_l3_power_w" in parsed["fields"]

    @pytest.mark.anyio
    async def test_huawei_point_tags(self):
        """ems_huawei point must have role and available tags."""
        writer, mock_post = _make_writer()
        h = _make_huawei_snapshot(available=True)
        v = _make_victron_snapshot()

        await writer.write_per_system_metrics(h, v, "PRIMARY_DISCHARGE", "SECONDARY_DISCHARGE")

        lines = _get_posted_lines(mock_post)
        huawei_line = [l for l in lines if l.startswith("ems_huawei")][0]
        parsed = _parse_line(huawei_line)
        assert parsed["tags"]["role"] == "PRIMARY_DISCHARGE"
        assert parsed["tags"]["available"] == "true"

    @pytest.mark.anyio
    async def test_fire_and_forget(self):
        """write_per_system_metrics must swallow exceptions."""
        writer, mock_post = _make_writer()
        mock_post.side_effect = Exception("connection refused")
        h = _make_huawei_snapshot()
        v = _make_victron_snapshot()

        # Must not raise
        await writer.write_per_system_metrics(h, v, "PRIMARY_DISCHARGE", "SECONDARY_DISCHARGE")


# ---------------------------------------------------------------------------
# write_decision tests
# ---------------------------------------------------------------------------


class TestWriteDecision:
    @pytest.mark.anyio
    async def test_writes_ems_decision_point(self):
        """write_decision must write an ems_decision point."""
        writer, mock_post = _make_writer()
        entry = DecisionEntry(
            timestamp="2026-03-22T12:00:00Z",
            trigger="role_change",
            huawei_role="PRIMARY_DISCHARGE",
            victron_role="SECONDARY_DISCHARGE",
            p_target_w=-5000.0,
            huawei_allocation_w=-3000.0,
            victron_allocation_w=-2000.0,
            pool_status="NORMAL",
            reasoning="Huawei higher SoC",
        )

        await writer.write_decision(entry)

        mock_post.assert_called_once()
        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        assert parsed["measurement"] == "ems_decision"

    @pytest.mark.anyio
    async def test_trigger_as_tag(self):
        """trigger must be a tag, not a field."""
        writer, mock_post = _make_writer()
        entry = DecisionEntry(
            timestamp="2026-03-22T12:00:00Z",
            trigger="failover",
            huawei_role="HOLDING",
            victron_role="PRIMARY_DISCHARGE",
            p_target_w=-5000.0,
            huawei_allocation_w=0.0,
            victron_allocation_w=-5000.0,
            pool_status="DEGRADED",
            reasoning="Huawei offline",
        )

        await writer.write_decision(entry)

        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        assert parsed["tags"]["trigger"] == "failover"

    @pytest.mark.anyio
    async def test_roles_as_string_fields(self):
        """huawei_role and victron_role must be string fields (quoted)."""
        writer, mock_post = _make_writer()
        entry = DecisionEntry(
            timestamp="2026-03-22T12:00:00Z",
            trigger="role_change",
            huawei_role="PRIMARY_DISCHARGE",
            victron_role="CHARGING",
            p_target_w=-3000.0,
            huawei_allocation_w=-3000.0,
            victron_allocation_w=0.0,
            pool_status="NORMAL",
            reasoning="Test",
        )

        await writer.write_decision(entry)

        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        # String fields are double-quoted in line protocol
        assert parsed["fields"]["huawei_role"] == '"PRIMARY_DISCHARGE"'
        assert parsed["fields"]["victron_role"] == '"CHARGING"'
        # Must NOT be in tags
        assert "huawei_role" not in parsed["tags"]
        assert "victron_role" not in parsed["tags"]

    @pytest.mark.anyio
    async def test_fire_and_forget(self):
        """write_decision must swallow exceptions."""
        writer, mock_post = _make_writer()
        mock_post.side_effect = Exception("timeout")
        entry = DecisionEntry(
            timestamp="2026-03-22T12:00:00Z",
            trigger="role_change",
            huawei_role="HOLDING",
            victron_role="HOLDING",
            p_target_w=0.0,
            huawei_allocation_w=0.0,
            victron_allocation_w=0.0,
            pool_status="OFFLINE",
            reasoning="Both offline",
        )

        # Must not raise
        await writer.write_decision(entry)


# ---------------------------------------------------------------------------
# write_coordinator_state tests
# ---------------------------------------------------------------------------


class TestWriteCoordinatorState:
    @pytest.mark.anyio
    async def test_writes_ems_system_point(self):
        """write_coordinator_state must write an ems_system point."""
        writer, mock_post = _make_writer()
        state = _make_coordinator_state()

        await writer.write_coordinator_state(state)

        mock_post.assert_called_once()
        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        assert parsed["measurement"] == "ems_system"

    @pytest.mark.anyio
    async def test_control_state_as_string(self):
        """control_state must be used as plain string (not .value)."""
        writer, mock_post = _make_writer()
        state = _make_coordinator_state(control_state="DISCHARGE")

        await writer.write_coordinator_state(state)

        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        assert parsed["tags"]["control_state"] == "DISCHARGE"

    @pytest.mark.anyio
    async def test_has_role_and_pool_tags(self):
        """ems_system point must include huawei_role, victron_role, pool_status as tags."""
        writer, mock_post = _make_writer()
        state = _make_coordinator_state(
            huawei_role="CHARGING",
            victron_role="HOLDING",
            pool_status="DEGRADED",
        )

        await writer.write_coordinator_state(state)

        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        assert parsed["tags"]["huawei_role"] == "CHARGING"
        assert parsed["tags"]["victron_role"] == "HOLDING"
        assert parsed["tags"]["pool_status"] == "DEGRADED"

    @pytest.mark.anyio
    async def test_existing_write_system_state_still_works(self):
        """Existing write_system_state must still work unchanged with UnifiedPoolState."""
        writer, mock_post = _make_writer()
        state = _make_state()

        await writer.write_system_state(state)

        mock_post.assert_called_once()
        lines = _get_posted_lines(mock_post)
        parsed = _parse_line(lines[0])
        assert parsed["measurement"] == "ems_system"

    @pytest.mark.anyio
    async def test_fire_and_forget(self):
        """write_coordinator_state must swallow exceptions."""
        writer, mock_post = _make_writer()
        mock_post.side_effect = Exception("timeout")
        state = _make_coordinator_state()

        # Must not raise
        await writer.write_coordinator_state(state)
