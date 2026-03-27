"""Unit tests for backend/drivers/emma_driver.py.

Tests cover:
- _decode_u32 / _decode_i32 static helpers
- _read_all register decode paths (pv_power_w, feed_in_power_w, battery_soc_pct,
  ess_control_mode) via per-call side_effect on read_holding_registers
- poll() fire-and-forget error handling
- connect() / close() delegation to the underlying pymodbus client
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.drivers.emma_driver import (
    EmmaDriver,
    EmmaSnapshot,
    _decode_u32,
    _decode_i32,
    _REGISTER_MAP,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_N_MAIN_REGISTERS = len(_REGISTER_MAP)  # currently 9


def _ok_result(registers: list[int]) -> MagicMock:
    """Return a mock pymodbus result with registers and no error."""
    r = MagicMock()
    r.isError.return_value = False
    r.registers = registers
    return r


def _err_result() -> MagicMock:
    """Return a mock pymodbus result that signals an error."""
    r = MagicMock()
    r.isError.return_value = True
    return r


def _make_driver() -> tuple[EmmaDriver, AsyncMock]:
    """Create an EmmaDriver with a patched AsyncModbusTcpClient.

    Returns the driver and the mock client instance for further configuration.
    """
    with patch(
        "backend.drivers.emma_driver.AsyncModbusTcpClient",
        autospec=True,
    ) as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.connect = AsyncMock()
        mock_client.close = MagicMock()
        mock_client.read_holding_registers = AsyncMock()
        driver = EmmaDriver(host="127.0.0.1", port=502)
        return driver, mock_client


# ---------------------------------------------------------------------------
# Static decode helpers
# ---------------------------------------------------------------------------


def test_decode_u32_basic() -> None:
    """Two registers encode an unsigned 32-bit value big-endian."""
    # 0x0001_0000 == 65536
    assert _decode_u32([0x0001, 0x0000]) == 65536


def test_decode_u32_max_word() -> None:
    assert _decode_u32([0xFFFF, 0xFFFF]) == 0xFFFF_FFFF


def test_decode_u32_zero() -> None:
    assert _decode_u32([0x0000, 0x0000]) == 0


def test_decode_i32_positive() -> None:
    # 0x0000_0064 == 100
    assert _decode_i32([0x0000, 0x0064]) == 100


def test_decode_i32_negative() -> None:
    # -1 in two's complement 32-bit is 0xFFFF_FFFF
    assert _decode_i32([0xFFFF, 0xFFFF]) == -1


def test_decode_i32_negative_large() -> None:
    # -1_000_000 in two's complement 32-bit is 0xFFF0_BDC0
    # hi=0xFFF0, lo=0xBDC0
    assert _decode_i32([0xFFF0, 0xBDC0]) == -1_000_000


# ---------------------------------------------------------------------------
# _read_all / full snapshot
# ---------------------------------------------------------------------------


def _build_side_effects(
    *,
    pv_raw: int = 1_500_000,      # 1500 W  (U32, gain=1000)
    load_raw: int = 800_000,       # 800 W   (U32, gain=1000)
    feed_in_raw: int = 700_000,    # 700 W   (I32, gain=1000, positive=export)
    battery_raw: int = 0,          # 0 W     (I32, gain=1000)
    soc_raw: int = 7500,           # 75 %    (U16, gain=100)
    pv_today_raw: int = 1_000_000, # 10 kWh  (U32, gain=100) — but 100 not 1000, so 10000 kWh? no: 1_000_000/100=10000 kWh seems wrong
    cons_today_raw: int = 50000,   # 500 kWh (U32, gain=100)
    charged_raw: int = 20000,      # 200 kWh (U32, gain=100)
    discharged_raw: int = 15000,   # 150 kWh (U32, gain=100)
    ess_mode: int = 2,
) -> list[MagicMock]:
    """Build the ordered list of mock results matching _REGISTER_MAP + ESS mode.

    Order in _REGISTER_MAP:
      [0] pv_power_w        U32 gain=1000
      [1] load_power_w      U32 gain=1000
      [2] feed_in_power_w   I32 gain=1000
      [3] battery_power_w   I32 gain=1000
      [4] battery_soc_pct   U16 gain=100
      [5] pv_yield_today_kwh  U32 gain=100
      [6] consumption_today_kwh U32 gain=100
      [7] charged_today_kwh   U32 gain=100
      [8] discharged_today_kwh U32 gain=100
      [9] ess_control_mode  U16 (separate call)
    """
    def u32_regs(v: int) -> list[int]:
        return [(v >> 16) & 0xFFFF, v & 0xFFFF]

    def i32_regs(v: int) -> list[int]:
        if v < 0:
            v = v + 0x1_0000_0000
        return [(v >> 16) & 0xFFFF, v & 0xFFFF]

    return [
        _ok_result(u32_regs(pv_raw)),
        _ok_result(u32_regs(load_raw)),
        _ok_result(i32_regs(feed_in_raw)),
        _ok_result(i32_regs(battery_raw)),
        _ok_result([soc_raw]),               # U16 single register
        _ok_result(u32_regs(pv_today_raw)),
        _ok_result(u32_regs(cons_today_raw)),
        _ok_result(u32_regs(charged_raw)),
        _ok_result(u32_regs(discharged_raw)),
        _ok_result([ess_mode]),              # ESS mode U16
    ]


@pytest.mark.anyio
async def test_read_all_pv_power_w() -> None:
    """pv_power_w = U32 at 30354 / 1000 → watts (integer)."""
    driver, mock_client = _make_driver()
    # 1500 raw units / 1000 = 1 W? No: raw = 1_500_000 / 1000 = 1500 W
    # raw value 1_500_000 → registers = [22, 57920] → int(1_500_000/1000) = 1500
    mock_client.read_holding_registers.side_effect = _build_side_effects(pv_raw=1_500_000)

    snap = await driver._read_all()

    assert snap.pv_power_w == 1500


@pytest.mark.anyio
async def test_read_all_feed_in_power_negative() -> None:
    """feed_in_power_w is I32; negative raw = grid import."""
    # -500 W import: raw = -500_000 (gain=1000), I32
    driver, mock_client = _make_driver()
    mock_client.read_holding_registers.side_effect = _build_side_effects(
        feed_in_raw=-500_000,
    )

    snap = await driver._read_all()

    assert snap.feed_in_power_w == -500


@pytest.mark.anyio
async def test_read_all_battery_soc_pct() -> None:
    """battery_soc_pct U16 gain=100 → float percentage."""
    driver, mock_client = _make_driver()
    mock_client.read_holding_registers.side_effect = _build_side_effects(
        soc_raw=8250,  # 82.5 %
    )

    snap = await driver._read_all()

    assert abs(snap.battery_soc_pct - 82.5) < 0.01


@pytest.mark.anyio
async def test_read_all_ess_control_mode() -> None:
    """ess_control_mode is read from the separate ESS holding register."""
    driver, mock_client = _make_driver()
    mock_client.read_holding_registers.side_effect = _build_side_effects(ess_mode=5)

    snap = await driver._read_all()

    assert snap.ess_control_mode == 5


@pytest.mark.anyio
async def test_read_all_returns_emma_snapshot() -> None:
    """_read_all returns an EmmaSnapshot with the expected types."""
    driver, mock_client = _make_driver()
    mock_client.read_holding_registers.side_effect = _build_side_effects()

    snap = await driver._read_all()

    assert isinstance(snap, EmmaSnapshot)
    assert isinstance(snap.pv_power_w, int)
    assert isinstance(snap.battery_soc_pct, float)
    assert isinstance(snap.ess_control_mode, int)


@pytest.mark.anyio
async def test_read_all_correct_call_count() -> None:
    """_read_all makes exactly len(_REGISTER_MAP)+1 read_holding_registers calls."""
    driver, mock_client = _make_driver()
    mock_client.read_holding_registers.side_effect = _build_side_effects()

    await driver._read_all()

    assert mock_client.read_holding_registers.call_count == _N_MAIN_REGISTERS + 1


@pytest.mark.anyio
async def test_read_all_raises_on_error_result() -> None:
    """_read_all raises RuntimeError when any register read returns an error."""
    driver, mock_client = _make_driver()
    # First result is an error
    mock_client.read_holding_registers.side_effect = [_err_result()]

    with pytest.raises(RuntimeError, match="EMMA read error"):
        await driver._read_all()


@pytest.mark.anyio
async def test_read_all_raises_on_ess_error() -> None:
    """_read_all raises RuntimeError when the ESS control mode read fails."""
    driver, mock_client = _make_driver()
    # All 9 main registers succeed, ESS read fails
    side_effects = _build_side_effects()
    side_effects[-1] = _err_result()  # replace ESS mode result with error
    mock_client.read_holding_registers.side_effect = side_effects

    with pytest.raises(RuntimeError, match="EMMA read error"):
        await driver._read_all()


# ---------------------------------------------------------------------------
# poll() — fire-and-forget
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_poll_returns_snapshot_on_success() -> None:
    """poll() returns an EmmaSnapshot when _read_all succeeds."""
    driver, mock_client = _make_driver()
    mock_client.read_holding_registers.side_effect = _build_side_effects()

    result = await driver.poll()

    assert isinstance(result, EmmaSnapshot)


@pytest.mark.anyio
async def test_poll_returns_none_on_exception() -> None:
    """poll() returns None on communication failure (fire-and-forget)."""
    driver, mock_client = _make_driver()
    mock_client.read_holding_registers.side_effect = ConnectionError("refused")

    result = await driver.poll()

    assert result is None


@pytest.mark.anyio
async def test_poll_returns_none_on_modbus_error() -> None:
    """poll() returns None when a register read signals isError()."""
    driver, mock_client = _make_driver()
    mock_client.read_holding_registers.side_effect = [_err_result()]

    result = await driver.poll()

    assert result is None


# ---------------------------------------------------------------------------
# connect() / close()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_connect_delegates_to_client() -> None:
    """connect() calls the underlying AsyncModbusTcpClient.connect()."""
    driver, mock_client = _make_driver()

    await driver.connect()

    mock_client.connect.assert_awaited_once()


@pytest.mark.anyio
async def test_close_delegates_to_client() -> None:
    """close() calls the underlying AsyncModbusTcpClient.close()."""
    driver, mock_client = _make_driver()

    await driver.close()

    mock_client.close.assert_called_once()
