# Caliber Learnings

Accumulated patterns and anti-patterns from development sessions.
Auto-managed by [caliber](https://github.com/rely-ai-org/caliber) — do not edit manually.

- **[gotcha]** pymodbus 3.12 keyword-only args: use `device_id=` not `slave=` or `unit=` — the old names are deprecated and will fail silently or raise unexpected errors
- **[fix]** `RemoteTrigger` tool only accepts `name`, `schedule`, and the task prompt field — do NOT pass `description`, `allowed_tools`, or `prompt` as body keys. Use the `/schedule` skill instead of calling RemoteTrigger directly, as the skill knows the correct API shape
- **[pattern]** When adding a new Modbus driver, use raw `pymodbus.client.AsyncModbusTcpClient` — do NOT use the `huawei-solar` library for non-inverter devices (EMMA, meters). The huawei-solar register map doesn't cover them
- **[pattern]** Test suite takes ~4 minutes (`python -m pytest tests/ -q --tb=short -x`) — use `run_in_background` for pytest runs to avoid blocking
- **[env]** InfluxDB v1.8 community add-on: no auth required, query via `curl -s 'http://192.168.0.10:8086/query?db=ems' --data-urlencode 'q=...'`
- **[convention]** HA Add-on config changes require three files: `ems/config.yaml` (options + schema), `ems/run.sh` (env mapping), and `backend/config.py` (dataclass with `from_env()`)
