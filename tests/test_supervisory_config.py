from __future__ import annotations

import os
from unittest.mock import patch

from backend.config import SupervisoryConfig


class TestSupervisoryConfig:
    def test_defaults(self) -> None:
        cfg = SupervisoryConfig()
        assert cfg.control_mode == "supervisory"
        assert cfg.observation_interval_s == 5.0
        assert cfg.soc_balance_threshold_pct == 10.0
        assert cfg.soc_balance_hysteresis_pct == 5.0
        assert cfg.min_soc_pct == 10.0
        assert cfg.min_soc_hysteresis_pct == 5.0

    def test_from_env(self) -> None:
        env = {
            "EMS_CONTROL_MODE": "legacy",
            "EMS_OBSERVATION_INTERVAL_S": "10",
            "EMS_SOC_BALANCE_THRESHOLD_PCT": "15",
            "EMS_SOC_BALANCE_HYSTERESIS_PCT": "7",
            "EMS_MIN_SOC_PCT": "12",
            "EMS_MIN_SOC_HYSTERESIS_PCT": "3",
        }
        with patch.dict(os.environ, env):
            cfg = SupervisoryConfig.from_env()
        assert cfg.control_mode == "legacy"
        assert cfg.observation_interval_s == 10.0
        assert cfg.soc_balance_threshold_pct == 15.0
        assert cfg.soc_balance_hysteresis_pct == 7.0
        assert cfg.min_soc_pct == 12.0
        assert cfg.min_soc_hysteresis_pct == 3.0

    def test_from_env_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            cfg = SupervisoryConfig.from_env()
        assert cfg.control_mode == "supervisory"
        assert cfg.observation_interval_s == 5.0
