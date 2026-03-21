# EMS – Energy Management System

Orchestrates battery dispatch across a **Huawei SUN2000** inverter and a **Victron MultiPlus II** system. Coordinates with EVCC for EV charging, publishes metrics to InfluxDB, and integrates with Home Assistant sensors and MQTT.

---

## Quick setup

1. Install the add-on and open **Configuration**.
2. Set the four required fields: **Huawei Modbus Host**, **Victron Cerbo GX Host**, **InfluxDB URL**, and **InfluxDB API Token**.
3. Set port/unit-ID overrides if your system differs from the defaults (see below).
4. Click **Save** then **Start**.
5. Open the web interface on port 8000 and complete the setup wizard.

---

## Modbus unit IDs

Huawei inverters use Modbus unit IDs to identify individual devices on the bus. The defaults (master=1, slave=2) work for most standalone installations. If you have a custom configuration or are using a `modbus-proxy` add-on, probe the correct values first:

```bash
# From a machine on your LAN, scan unit IDs 0-10 for register 32000
# (Huawei SUN2000 model register — responds on inverter unit IDs only)
python3 -c "
import socket, struct
for uid in range(0, 10):
    try:
        s = socket.create_connection(('YOUR_MODBUS_HOST', 502), timeout=2)
        pdu = struct.pack('>BBHH', uid, 3, 32000, 1)
        s.sendall(struct.pack('>HHH', 1, 0, len(pdu)) + pdu)
        r = s.recv(64); s.close()
        if len(r) >= 8 and r[7] == 3:
            print(f'Unit {uid}: OK')
    except: pass
"
```

## Modbus proxy (modbus-proxy add-on)

If you use the **modbus-proxy** add-on alongside EMS:

- Enable `host_network: true` on this add-on (already the default).
- Set **Huawei Modbus Host** to `127.0.0.1` — the proxy is reachable on the host network at localhost.
- The proxy's upstream should point to your inverter's LAN IP.

---

## Authentication (optional)

The EMS web interface has optional login protection. Leave the field blank to disable — appropriate if your LAN is trusted or access is controlled another way.

To enable, generate a bcrypt password hash and paste it into **Admin Password Hash (bcrypt)**:

```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"
```

The session signing key is generated automatically on first startup and stored in `/config/.jwt_secret`. No operator action required.

---

## Health endpoint

`GET http://<haos-ip>:8000/api/health` returns the current system status. The add-on watchdog polls this automatically and will restart the add-on if it becomes unreachable.

---

## Data persistence

The EMS setup wizard saves its configuration to `/config/ems_config.json` inside the add-on, which maps to the HA config volume. This file survives add-on updates and restarts.
