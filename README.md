# toshiba-ac-console-wifi-module

Simple MicroPython bridge to control Toshiba CN22 indoor units via a Wi‑Fi connected ESP32.

This repo contains:

-   `src/` — MicroPython application (web server, Modbus TCP server, CN22 UART driver)
-   `src/lib/` — bundled MicroPython typing stubs and helper modules (no external mip install required)

## Quick device commands

Copy and run the CN22 write probe on the device (copies file then runs it in the REPL):

```bash
mpremote cp ./tools/cn22_write_probe.py :/cn22_write_probe.py && \mpremote soft-reset exec "import sys; sys.modules.pop('cn22_write_probe', None); import cn22_write_probe" repl
```

Copy and run the CN22 sniff script on the connected device (uses `connect auto`):

```bash
mpremote connect auto cp ./tools/cn22_sniff.py :/cn22_sniff.py && \mpremote soft-reset exec "import sys; sys.modules.pop('cn22_sniff', None); import cn22_sniff" repl
```

Remove all files from the connected device filesystem (use with caution):

```bash
mpremote connect auto fs rm -rv -rv :/
```

Copy the whole `src/` tree to the connected device:

```bash
mpremote connect auto cp -r ./src/* :
```

## Modbus helper tool (`tools/modbus_tool.py`)

This small host-side script connects to the Modbus TCP server running on the ESP32 and
can read the device status or write single registers (power, mode, fan, swing, setpoint).

Quick description:

-   Reads 8 registers by default: state, mode, fanmode, swing, setpoint_x10, roomtemp_x10, outdoort_x10, rssi_dbm.
-   Temperatures are reported as tenths of °C (e.g. 220 => 22.0 °C). Signed values are handled.
-   Supports single-register writes via `--write-func` and `--value`.

CLI summary (all shown by `--help`):

-   `ip` (positional): Modbus TCP server IP (required).
-   `--port`: TCP port (default 502).
-   `--unit`: Modbus unit/slave id (default 1).
-   `--interval`: polling interval seconds (default 2.0).
-   `--address`: start register (default 0).
-   `--count`: number of registers to read (default 8).
-   `--once`: read once and exit.
-   `--write-func`: one of `state|mode|fanmode|swing|setpoint` to perform a write.
-   `--value`: value to write (required with `--write-func`). For `setpoint` pass a decimal (e.g. 21.5).

Write function semantics:

-   `state`: 0 or 1.
-   `mode`: 0..4 (0=AUTO,1=COOL,2=HEAT,3=DRY,4=FAN).
-   `fanmode`: 0..6 (0=QUIET,6=AUTO).
-   `swing`: 0 or 1.
-   `setpoint`: temperature in °C (float allowed) — script converts to tenths and writes integer.

## Examples (copy-paste ready)

# Read status once (replace <MODBUS_IP> with your device IP)

```bash
python tools/modbus_tool.py <MODBUS_IP> --once
```

# Poll status every 5s (replace <MODBUS_IP> with your device IP)

```bash
python tools/modbus_tool.py <MODBUS_IP> --interval 5.0
```

# Write mode = HEAT (2) (replace <MODBUS_IP> with your device IP)

```bash
python tools/modbus_tool.py <MODBUS_IP> --write-func mode --value 2
```

# Set setpoint to 22.0 °C (replace <MODBUS_IP> with your device IP)

```bash
python tools/modbus_tool.py <MODBUS_IP> --write-func setpoint --value 22.0
```

# Turn power off (replace <MODBUS_IP> with your device IP)

```bash
python tools/modbus_tool.py <MODBUS_IP> --write-func state --value 0
```

## Notes

-   Requires `pymodbus` in the host Python environment (pip install pymodbus).
-   The project already includes MicroPython stubs in `src/lib/`, so installing them with `mip` is optional.
-   If you hit connection or read errors, verify IP, port, unit id and firewall settings.
