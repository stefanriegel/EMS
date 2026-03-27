"""Unit tests for the Victron Modbus TCP driver.

All tests mock ``pymodbus.client.AsyncModbusTcpClient`` at the import site.
No live hardware or network connection required.

Coverage:
  - VictronDriver connect/close lifecycle
  - read_system_state() register reading with scale factors and signed int16
  - write_ac_power_setpoint() Hub4 register writes
  - _with_reconnect retry logic
  - Sign convention: positive battery_power_w = charging
  - Configurable unit IDs (system_unit_id, vebus_unit_id)
  - VictronPhaseData / VictronSystemData dataclass contracts (retained)
"""
from __future__ import annotations

import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.drivers.victron_models import VictronPhaseData, VictronSystemData


# ---------------------------------------------------------------------------
# Helpers (retained from v1 tests)
# ---------------------------------------------------------------------------

def _make_phase(**overrides) -> VictronPhaseData:
    """Return a fully-populated VictronPhaseData with sensible defaults."""
    defaults: dict = {
        "power_w": 1000.0,
        "current_a": 4.4,
        "voltage_v": 230.0,
        "setpoint_w": None,
    }
    defaults.update(overrides)
    return VictronPhaseData(**defaults)


def _make_system_data(**overrides) -> VictronSystemData:
    """Return a fully-populated VictronSystemData with sensible defaults."""
    defaults: dict = {
        "battery_soc_pct": 60.0,
        "battery_power_w": 0.0,
        "battery_current_a": 0.0,
        "battery_voltage_v": 48.0,
        "l1": _make_phase(),
        "l2": _make_phase(),
        "l3": _make_phase(),
        "ess_mode": 3,
        "system_state": 9,
        "vebus_state": 9,
        "grid_power_w": None,
        "grid_l1_power_w": None,
        "grid_l2_power_w": None,
        "grid_l3_power_w": None,
        "consumption_w": None,
        "pv_on_grid_w": None,
        "timestamp": 12345.0,
    }
    defaults.update(overrides)
    return VictronSystemData(**defaults)


# ---------------------------------------------------------------------------
# Mock infrastructure for pymodbus
# ---------------------------------------------------------------------------

def _mock_register_response(registers: list[int]) -> MagicMock:
    """Return a mock pymodbus response with the given register values."""
    resp = MagicMock()
    resp.isError.return_value = False
    resp.registers = registers
    return resp


def _mock_error_response() -> MagicMock:
    """Return a mock pymodbus error response."""
    resp = MagicMock()
    resp.isError.return_value = True
    return resp


@pytest.fixture
def mock_client():
    """Return an AsyncMock mimicking pymodbus AsyncModbusTcpClient."""
    client = AsyncMock()
    client.connect = AsyncMock(return_value=True)
    client.close = MagicMock()
    client.read_holding_registers = AsyncMock()
    client.write_register = AsyncMock()
    return client


@pytest.fixture
def driver(mock_client):
    """Return a VictronDriver with mocked pymodbus client."""
    from backend.drivers.victron_driver import VictronDriver

    with patch(
        "backend.drivers.victron_driver.AsyncModbusTcpClient",
        return_value=mock_client,
    ):
        d = VictronDriver(host="192.168.0.10", port=502)
        # Pre-configure health check for connect()
        mock_client.read_holding_registers.return_value = _mock_register_response([60])
        yield d


# ---------------------------------------------------------------------------
# Dataclass contract tests (retained from v1)
# ---------------------------------------------------------------------------

class TestVictronPhaseData:
    def test_construction_all_fields(self):
        """All four fields are stored and retrieved after construction."""
        phase = VictronPhaseData(
            power_w=1500.0,
            current_a=6.5,
            voltage_v=231.0,
            setpoint_w=-500.0,
        )
        assert phase.power_w == pytest.approx(1500.0)
        assert phase.current_a == pytest.approx(6.5)
        assert phase.voltage_v == pytest.approx(231.0)
        assert phase.setpoint_w == pytest.approx(-500.0)

    def test_setpoint_w_accepts_none(self):
        phase = _make_phase(setpoint_w=None)
        assert phase.setpoint_w is None

    def test_setpoint_w_accepts_zero(self):
        phase = _make_phase(setpoint_w=0.0)
        assert phase.setpoint_w == pytest.approx(0.0)


class TestVictronSystemData:
    def test_construction_typical_values(self):
        data = _make_system_data(
            battery_soc_pct=75.0,
            battery_power_w=2500.0,
            battery_current_a=52.0,
            battery_voltage_v=48.2,
        )
        assert data.battery_soc_pct == pytest.approx(75.0)
        assert data.battery_power_w == pytest.approx(2500.0)
        assert data.battery_current_a == pytest.approx(52.0)
        assert data.battery_voltage_v == pytest.approx(48.2)

    def test_charging_positive_battery_power(self):
        data = _make_system_data(battery_power_w=3000.0)
        assert data.charge_power_w == pytest.approx(3000.0)
        assert data.discharge_power_w == pytest.approx(0.0)

    def test_discharging_negative_battery_power(self):
        data = _make_system_data(battery_power_w=-2000.0)
        assert data.charge_power_w == pytest.approx(0.0)
        assert data.discharge_power_w == pytest.approx(2000.0)

    def test_idle_zero_battery_power(self):
        data = _make_system_data(battery_power_w=0.0)
        assert data.charge_power_w == pytest.approx(0.0)
        assert data.discharge_power_w == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# TestVictronDriverConnect
# ---------------------------------------------------------------------------

class TestVictronDriverConnect:
    @pytest.mark.anyio
    async def test_connect_success(self, driver, mock_client):
        """connect() calls client.connect() and health-check register 843."""
        await driver.connect()
        mock_client.connect.assert_called_once()
        # Health check reads SoC register 843
        mock_client.read_holding_registers.assert_called()

    @pytest.mark.anyio
    async def test_connect_failure(self, mock_client):
        """connect() raises ConnectionError if client.connect() returns False."""
        from backend.drivers.victron_driver import VictronDriver

        mock_client.connect = AsyncMock(return_value=False)
        with patch(
            "backend.drivers.victron_driver.AsyncModbusTcpClient",
            return_value=mock_client,
        ):
            d = VictronDriver(host="192.168.0.10", port=502)
            with pytest.raises(ConnectionError):
                await d.connect()

    @pytest.mark.anyio
    async def test_connect_health_check_fails(self, mock_client):
        """connect() raises ConnectionError if health check register read fails."""
        from backend.drivers.victron_driver import VictronDriver

        mock_client.connect = AsyncMock(return_value=True)
        mock_client.read_holding_registers = AsyncMock(
            return_value=_mock_error_response()
        )
        with patch(
            "backend.drivers.victron_driver.AsyncModbusTcpClient",
            return_value=mock_client,
        ):
            d = VictronDriver(host="192.168.0.10", port=502)
            with pytest.raises(ConnectionError):
                await d.connect()


# ---------------------------------------------------------------------------
# TestVictronDriverRead
# ---------------------------------------------------------------------------

class TestVictronDriverRead:
    def _configure_read_responses(self, mock_client):
        """Configure mock to return known register values for all batches.

        Batches (in order called by read_system_state):
        1. System battery: regs 840-843 -> [482, 52, 1500, 60]
           (voltage=48.2V, current=5.2A, power=1500W, SoC=60%)
        2. System grid: regs 820-822 -> [100, 200, 65486]
           (L1=100W, L2=200W, L3=-50W as unsigned)
        3. System PV-on-grid: regs 808-810 -> [500, 600, 400]
           (L1=500W, L2=600W, L3=400W, total=1500W)
        4. System consumption: regs 817-819 -> [1200, 1500, 1100]
           (L1=1200W, L2=1500W, L3=1100W, total=3800W)
        5. VE.Bus voltage: regs 15-17 -> [2300, 2310, 2290]
           (L1=230.0V, L2=231.0V, L3=229.0V)
        6. VE.Bus current: regs 18-20 -> [43, 52, 56]
           (L1=4.3A, L2=5.2A, L3=5.6A)
        7. VE.Bus power: regs 23-25 -> [15000, 12000, 13000]
           (L1=1500.0W, L2=1200.0W, L3=1300.0W after *0.1 scale)
        8. VE.Bus state: reg 31 -> [9]
        9. VE.Bus mode: reg 33 -> [3]
        """
        def read_handler(address, count=1, device_id=0):
            responses = {
                (840, 4, 100): _mock_register_response([482, 52, 1500, 60]),
                (820, 3, 100): _mock_register_response([100, 200, 65486]),
                (808, 3, 100): _mock_register_response([500, 600, 400]),
                (817, 3, 100): _mock_register_response([1200, 1500, 1100]),
                (15, 3, 227): _mock_register_response([2300, 2310, 2290]),
                (18, 3, 227): _mock_register_response([43, 52, 56]),
                (23, 3, 227): _mock_register_response([15000, 12000, 13000]),
                (31, 1, 227): _mock_register_response([9]),
                (33, 1, 227): _mock_register_response([3]),
            }
            key = (address, count, device_id)
            if key in responses:
                return responses[key]
            raise ValueError(f"Unexpected read: address={address} count={count} device_id={device_id}")

        mock_client.read_holding_registers = AsyncMock(side_effect=read_handler)

    @pytest.mark.anyio
    async def test_read_system_state_full(self, driver, mock_client):
        """read_system_state returns correct scaled values from register data."""
        self._configure_read_responses(mock_client)

        state = await driver.read_system_state()

        # Battery
        assert state.battery_voltage_v == pytest.approx(48.2)
        assert state.battery_current_a == pytest.approx(5.2)
        assert state.battery_power_w == pytest.approx(1500.0)
        assert state.battery_soc_pct == pytest.approx(60.0)

        # Grid
        assert state.grid_l1_power_w == pytest.approx(100.0)
        assert state.grid_l2_power_w == pytest.approx(200.0)
        assert state.grid_l3_power_w == pytest.approx(-50.0)

        # AC output
        assert state.l1.voltage_v == pytest.approx(230.0)
        assert state.l1.current_a == pytest.approx(4.3)
        assert state.l1.power_w == pytest.approx(1500.0)

        assert state.l2.voltage_v == pytest.approx(231.0)
        assert state.l2.current_a == pytest.approx(5.2)
        assert state.l2.power_w == pytest.approx(1200.0)

        assert state.l3.voltage_v == pytest.approx(229.0)
        assert state.l3.current_a == pytest.approx(5.6)
        assert state.l3.power_w == pytest.approx(1300.0)

        # Consumption
        assert state.consumption_w == pytest.approx(3800.0)  # 1200+1500+1100

        # PV on grid
        assert state.pv_on_grid_w == pytest.approx(1500.0)  # 500+600+400

        # ESS state
        assert state.vebus_state == 9
        assert state.ess_mode == 3

    @pytest.mark.anyio
    async def test_read_system_state_signed16(self, driver, mock_client):
        """Register value 65036 (0xFE0C) decodes to -500 for battery_power_w."""
        def read_handler(address, count=1, device_id=0):
            responses = {
                (840, 4, 100): _mock_register_response([482, 52, 65036, 60]),
                (820, 3, 100): _mock_register_response([0, 0, 0]),
                (808, 3, 100): _mock_register_response([0, 0, 0]),
                (817, 3, 100): _mock_register_response([0, 0, 0]),
                (15, 3, 227): _mock_register_response([2300, 2300, 2300]),
                (18, 3, 227): _mock_register_response([0, 0, 0]),
                (23, 3, 227): _mock_register_response([0, 0, 0]),
                (31, 1, 227): _mock_register_response([0]),
                (33, 1, 227): _mock_register_response([0]),
            }
            return responses.get((address, count, device_id), _mock_register_response([0]))

        mock_client.read_holding_registers = AsyncMock(side_effect=read_handler)

        state = await driver.read_system_state()
        assert state.battery_power_w == pytest.approx(-500.0)

    @pytest.mark.anyio
    async def test_read_system_state_consumption_populated(self, driver, mock_client):
        """consumption_w is now populated from regs 817-819 (L1+L2+L3 sum).
        pv_on_grid_w is populated from regs 808-810."""
        self._configure_read_responses(mock_client)

        state = await driver.read_system_state()
        # Default fixture: consumption regs 817-819 = [1200, 1500, 1100]
        assert state.consumption_w == pytest.approx(3800.0)
        # pv_on_grid_w: regs 808-810 = [500, 600, 400] → 1500 W
        assert state.pv_on_grid_w == pytest.approx(1500.0)

    @pytest.mark.anyio
    async def test_pv_on_grid_w_is_sum_of_three_phases(self, driver, mock_client):
        """pv_on_grid_w = sum of regs 808, 809, 810 as signed int16 (W)."""
        def read_handler(address, count=1, device_id=0):
            responses = {
                (840, 4, 100): _mock_register_response([482, 52, 1500, 60]),
                (820, 3, 100): _mock_register_response([0, 0, 0]),
                # PV: L1=1000W, L2=800W, L3=600W → total=2400W
                (808, 3, 100): _mock_register_response([1000, 800, 600]),
                (817, 3, 100): _mock_register_response([0, 0, 0]),
                (15, 3, 227): _mock_register_response([2300, 2300, 2300]),
                (18, 3, 227): _mock_register_response([0, 0, 0]),
                (23, 3, 227): _mock_register_response([0, 0, 0]),
                (31, 1, 227): _mock_register_response([0]),
                (33, 1, 227): _mock_register_response([0]),
            }
            return responses.get((address, count, device_id), _mock_register_response([0]))

        mock_client.read_holding_registers = AsyncMock(side_effect=read_handler)

        state = await driver.read_system_state()
        assert state.pv_on_grid_w == pytest.approx(2400.0)

    @pytest.mark.anyio
    async def test_pv_on_grid_w_zero_when_no_pv(self, driver, mock_client):
        """pv_on_grid_w is 0.0 when all PV registers read zero."""
        def read_handler(address, count=1, device_id=0):
            responses = {
                (840, 4, 100): _mock_register_response([482, 52, 0, 60]),
                (820, 3, 100): _mock_register_response([0, 0, 0]),
                (808, 3, 100): _mock_register_response([0, 0, 0]),
                (817, 3, 100): _mock_register_response([0, 0, 0]),
                (15, 3, 227): _mock_register_response([2300, 2300, 2300]),
                (18, 3, 227): _mock_register_response([0, 0, 0]),
                (23, 3, 227): _mock_register_response([0, 0, 0]),
                (31, 1, 227): _mock_register_response([0]),
                (33, 1, 227): _mock_register_response([0]),
            }
            return responses.get((address, count, device_id), _mock_register_response([0]))

        mock_client.read_holding_registers = AsyncMock(side_effect=read_handler)

        state = await driver.read_system_state()
        assert state.pv_on_grid_w == pytest.approx(0.0)

    @pytest.mark.anyio
    async def test_read_system_state_timestamp(self, driver, mock_client):
        """timestamp is set to a positive value from time.monotonic()."""
        self._configure_read_responses(mock_client)

        before = time.monotonic()
        state = await driver.read_system_state()
        after = time.monotonic()

        assert before <= state.timestamp <= after


# ---------------------------------------------------------------------------
# TestVictronDriverWrite
# ---------------------------------------------------------------------------

class TestVictronDriverWrite:
    @pytest.mark.anyio
    async def test_write_setpoint_l1(self, driver, mock_client):
        """write_ac_power_setpoint(1, -500.0) writes reg 37 with 0xFE0C to vebus unit."""
        await driver.connect()
        await driver.write_ac_power_setpoint(1, -500.0)
        mock_client.write_register.assert_called_with(
            address=37, value=0xFE0C, device_id=227,
        )

    @pytest.mark.anyio
    async def test_write_setpoint_l2(self, driver, mock_client):
        """write_ac_power_setpoint(2, 300.0) writes reg 40 with value 300."""
        await driver.connect()
        await driver.write_ac_power_setpoint(2, 300.0)
        mock_client.write_register.assert_called_with(
            address=40, value=300, device_id=227,
        )

    @pytest.mark.anyio
    async def test_write_setpoint_l3(self, driver, mock_client):
        """write_ac_power_setpoint(3, 0.0) writes reg 41 with value 0."""
        await driver.connect()
        await driver.write_ac_power_setpoint(3, 0.0)
        mock_client.write_register.assert_called_with(
            address=41, value=0, device_id=227,
        )


# ---------------------------------------------------------------------------
# TestVictronDriverReconnect
# ---------------------------------------------------------------------------

class TestVictronDriverReconnect:
    @pytest.mark.anyio
    async def test_with_reconnect_retries_once(self, driver, mock_client):
        """_with_reconnect retries once on ModbusException then succeeds."""
        from pymodbus.exceptions import ModbusException

        await driver.connect()

        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ModbusException("transient failure")
            return "ok"

        # Re-configure health check for reconnect
        mock_client.read_holding_registers.return_value = _mock_register_response([60])
        result = await driver._with_reconnect(flaky)
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.anyio
    async def test_with_reconnect_raises_on_second_failure(self, driver, mock_client):
        """_with_reconnect raises if both attempts fail."""
        from pymodbus.exceptions import ModbusException

        await driver.connect()

        async def always_fail():
            raise ModbusException("persistent failure")

        mock_client.read_holding_registers.return_value = _mock_register_response([60])
        with pytest.raises(ModbusException):
            await driver._with_reconnect(always_fail)


# ---------------------------------------------------------------------------
# TestVictronDriverSignConvention
# ---------------------------------------------------------------------------

class TestVictronDriverSignConvention:
    @pytest.mark.anyio
    async def test_positive_battery_power_is_charging(self, driver, mock_client):
        """Raw register 842 = 1500 -> battery_power_w = 1500 (charging)."""
        def read_handler(address, count=1, device_id=0):
            responses = {
                (840, 4, 100): _mock_register_response([480, 50, 1500, 60]),
                (820, 3, 100): _mock_register_response([0, 0, 0]),
                (808, 3, 100): _mock_register_response([0, 0, 0]),
                (817, 3, 100): _mock_register_response([0, 0, 0]),
                (15, 3, 227): _mock_register_response([2300, 2300, 2300]),
                (18, 3, 227): _mock_register_response([0, 0, 0]),
                (23, 3, 227): _mock_register_response([0, 0, 0]),
                (31, 1, 227): _mock_register_response([0]),
                (33, 1, 227): _mock_register_response([0]),
            }
            return responses.get((address, count, device_id), _mock_register_response([0]))

        mock_client.read_holding_registers = AsyncMock(side_effect=read_handler)

        state = await driver.read_system_state()
        assert state.battery_power_w == pytest.approx(1500.0)
        assert state.charge_power_w == pytest.approx(1500.0)
        assert state.discharge_power_w == pytest.approx(0.0)

    @pytest.mark.anyio
    async def test_negative_battery_power_is_discharging(self, driver, mock_client):
        """Raw register 842 = 0xFA24 (-1500) -> battery_power_w = -1500 (discharging)."""
        def read_handler(address, count=1, device_id=0):
            # 0xFA24 = 64036 unsigned = -1500 signed
            responses = {
                (840, 4, 100): _mock_register_response([480, 50, 64036, 60]),
                (820, 3, 100): _mock_register_response([0, 0, 0]),
                (808, 3, 100): _mock_register_response([0, 0, 0]),
                (817, 3, 100): _mock_register_response([0, 0, 0]),
                (15, 3, 227): _mock_register_response([2300, 2300, 2300]),
                (18, 3, 227): _mock_register_response([0, 0, 0]),
                (23, 3, 227): _mock_register_response([0, 0, 0]),
                (31, 1, 227): _mock_register_response([0]),
                (33, 1, 227): _mock_register_response([0]),
            }
            return responses.get((address, count, device_id), _mock_register_response([0]))

        mock_client.read_holding_registers = AsyncMock(side_effect=read_handler)

        state = await driver.read_system_state()
        assert state.battery_power_w == pytest.approx(-1500.0)
        assert state.charge_power_w == pytest.approx(0.0)
        assert state.discharge_power_w == pytest.approx(1500.0)


# ---------------------------------------------------------------------------
# TestVictronDriverUnitIds
# ---------------------------------------------------------------------------

class TestVictronDriverUnitIds:
    @pytest.mark.anyio
    async def test_custom_unit_ids(self, mock_client):
        """Custom unit IDs are passed to read_holding_registers as device_id."""
        from backend.drivers.victron_driver import VictronDriver

        with patch(
            "backend.drivers.victron_driver.AsyncModbusTcpClient",
            return_value=mock_client,
        ):
            d = VictronDriver(
                host="192.168.0.10",
                port=502,
                system_unit_id=101,
                vebus_unit_id=228,
            )

        # Set up read responses with the custom unit IDs
        def read_handler(address, count=1, device_id=0):
            responses = {
                (843, 1, 101): _mock_register_response([60]),  # health check
                (840, 4, 101): _mock_register_response([480, 50, 0, 60]),
                (820, 3, 101): _mock_register_response([0, 0, 0]),
                (808, 3, 101): _mock_register_response([0, 0, 0]),
                (817, 3, 101): _mock_register_response([0, 0, 0]),
                (15, 3, 228): _mock_register_response([2300, 2300, 2300]),
                (18, 3, 228): _mock_register_response([0, 0, 0]),
                (23, 3, 228): _mock_register_response([0, 0, 0]),
                (31, 1, 228): _mock_register_response([0]),
                (33, 1, 228): _mock_register_response([0]),
            }
            key = (address, count, device_id)
            if key in responses:
                return responses[key]
            raise ValueError(
                f"Unexpected read: address={address} count={count} device_id={device_id}"
            )

        mock_client.read_holding_registers = AsyncMock(side_effect=read_handler)

        await d.connect()
        state = await d.read_system_state()

        # If we get here without ValueError, the custom unit IDs were used correctly
        assert state.battery_soc_pct == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# TestVictronDriverLifecycle
# ---------------------------------------------------------------------------

class TestVictronDriverLifecycle:
    @pytest.mark.anyio
    async def test_close_calls_client_close(self, driver, mock_client):
        """close() calls client.close()."""
        await driver.connect()
        await driver.close()
        mock_client.close.assert_called_once()

    @pytest.mark.anyio
    async def test_context_manager(self, mock_client):
        """__aenter__ connects, __aexit__ closes."""
        from backend.drivers.victron_driver import VictronDriver

        mock_client.read_holding_registers.return_value = _mock_register_response([60])
        with patch(
            "backend.drivers.victron_driver.AsyncModbusTcpClient",
            return_value=mock_client,
        ):
            d = VictronDriver(host="192.168.0.10", port=502)
            async with d:
                mock_client.connect.assert_called_once()

        mock_client.close.assert_called_once()
