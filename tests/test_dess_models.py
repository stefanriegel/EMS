"""Tests for backend/dess_models.py — DessScheduleSlot, DessSchedule, VrmDiagnostics."""
from __future__ import annotations


class TestDessScheduleSlot:
    def test_all_defaults(self):
        from backend.dess_models import DessScheduleSlot
        s = DessScheduleSlot()
        assert s.soc_pct == 0.0
        assert s.start_s == 0
        assert s.duration_s == 0
        assert s.strategy == 0
        assert s.active is False

    def test_field_assignment(self):
        from backend.dess_models import DessScheduleSlot
        s = DessScheduleSlot(soc_pct=80.0, start_s=3600, duration_s=1800, strategy=1, active=True)
        assert s.soc_pct == 80.0
        assert s.start_s == 3600
        assert s.duration_s == 1800
        assert s.strategy == 1
        assert s.active is True

    def test_partial_override(self):
        from backend.dess_models import DessScheduleSlot
        s = DessScheduleSlot(strategy=2)
        assert s.strategy == 2
        assert s.soc_pct == 0.0
        assert s.active is False


class TestDessSchedule:
    def test_default_slot_count(self):
        from backend.dess_models import DessSchedule
        s = DessSchedule()
        assert len(s.slots) == 4

    def test_default_mode_and_last_update(self):
        from backend.dess_models import DessSchedule
        s = DessSchedule()
        assert s.mode == 0
        assert s.last_update == 0.0

    def test_slots_are_independent_instances(self):
        # Each DessSchedule gets its own slot list (not shared mutable default)
        from backend.dess_models import DessSchedule
        s1 = DessSchedule()
        s2 = DessSchedule()
        s1.slots[0].soc_pct = 99.0
        assert s2.slots[0].soc_pct == 0.0

    def test_custom_mode_and_slots(self):
        from backend.dess_models import DessSchedule, DessScheduleSlot
        slot = DessScheduleSlot(strategy=1, active=True)
        s = DessSchedule(slots=[slot], mode=1, last_update=1234.5)
        assert s.mode == 1
        assert s.last_update == 1234.5
        assert len(s.slots) == 1


class TestVrmDiagnostics:
    def test_all_none_defaults(self):
        from backend.dess_models import VrmDiagnostics
        d = VrmDiagnostics()
        assert d.battery_soc_pct is None
        assert d.battery_power_w is None
        assert d.grid_power_w is None
        assert d.pv_power_w is None
        assert d.consumption_w is None
        assert d.timestamp == 0.0

    def test_field_assignment_and_readback(self):
        from backend.dess_models import VrmDiagnostics
        d = VrmDiagnostics(
            battery_soc_pct=75.0,
            battery_power_w=-1500.0,
            grid_power_w=200.0,
            pv_power_w=3000.0,
            consumption_w=1700.0,
            timestamp=1700000000.0,
        )
        assert d.battery_soc_pct == 75.0
        assert d.battery_power_w == -1500.0
        assert d.grid_power_w == 200.0
        assert d.pv_power_w == 3000.0
        assert d.consumption_w == 1700.0
        assert d.timestamp == 1700000000.0
