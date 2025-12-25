# Toshiba AC Console Wifi Module

ESP32 bridge for controlling Toshiba control units over CN22 connector using Modbus TCP server.

This repo contains:

-   `src/` — MicroPython application (web server, Modbus TCP server, CN22 UART driver)
-   `src/lib/` — bundled MicroPython typing stubs and helper modules (no external mip install required)
-   `docs/` - additional files like schematics
-   `firmware/` - MicroPython binaries used on devices for this project
-   `tools/` - support tools to be run on developer computer or device it self

## Flashing MicroPython

You can use prebuild script for flashing MicroPython and copy source files onto device.

```bash
bash tools/flash_and_upload.sh
```

## Quick device commands (for development)

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

Copy the whole `src/` tree to the connected device, reset the device and enter repl:

```bash
mpremote connect auto cp -r ./src/* : + reset + repl
```

## Modbus helper tool (`tools/modbus_tool.py`)

This small host-side script connects to the Modbus TCP server running on the ESP32 and
can read the device status or write single registers (power, mode, fan, swing, setpoint,
other options, p-select).

Description:

-   Reads 10 registers by default: state, mode, fanmode, swing, setpoint_x10, roomtemp_x10, outdoort_x10, rssi_dbm, other, p_select.
-   Temperatures are reported as tenths of °C (e.g. 220 => 22.0 °C). Signed values are handled.
-   Supports single-register writes via `--write-func` and `--value`.

CLI summary (all shown by `--help`):

-   `ip` (positional): Modbus TCP server IP (required).
-   `--port`: TCP port (default 502).
-   `--unit`: Modbus unit/slave id (default 1).
-   `--interval`: polling interval seconds (default 2.0).
-   `--address`: start register (default 0).
-   `--count`: number of registers to read (default 10).
-   `--once`: read once and exit.
-   `--write-func`: one of `state|mode|fanmode|swing|setpoint|other|p_select` to perform a write.
-   `--value`: value to write (required with `--write-func`). For `setpoint` pass a decimal (e.g. 21.5).

Write function semantics:

-   `state`: 0 or 1.
-   `mode`: 0..4 (0=AUTO,1=COOL,2=HEAT,3=DRY,4=FAN).
-   `fanmode`: 0..6 (0=QUIET,6=AUTO).
-   `swing`: 0 or 1.
-   `setpoint`: temperature in °C (float allowed) — script converts to tenths and writes integer.
-   `other`: accepts names or hex (e.g. `eco`, `hi_power`, `outdoor_silent_on`, `outdoor_silent_mid`,
    `heat_exchange_on`, `heat_exchange_mid`, `floor`, `8deg`, `off` or `0x03`).
-   `p_select`: accepts `50|75|100` (percent) or hex (e.g. `0x4B`).

Modbus register map (0-based):

-   0: state
-   1: mode
-   2: fanmode
-   3: swing
-   4: setpoint_x10
-   5: roomtemp_x10
-   6: outdoort_x10
-   7: rssi_dbm
-   8: other
-   9: p_select

### Examples (copy-paste ready)

#### Read status once (replace <MODBUS_IP> with your device IP)

```bash
python tools/modbus_tool.py <MODBUS_IP> --once
```

#### Poll status every 5s (replace <MODBUS_IP> with your device IP)

```bash
python tools/modbus_tool.py <MODBUS_IP> --interval 5.0
```

#### Write mode = HEAT (2) (replace <MODBUS_IP> with your device IP)

```bash
python tools/modbus_tool.py <MODBUS_IP> --write-func mode --value 2
```

#### Set setpoint to 22.0 °C (replace <MODBUS_IP> with your device IP)

```bash
python tools/modbus_tool.py <MODBUS_IP> --write-func setpoint --value 22.0
```

#### Turn power off (replace <MODBUS_IP> with your device IP)

```bash
python tools/modbus_tool.py <MODBUS_IP> --write-func state --value 0
```

## CN22 protocol (brief)

Requests and responses used in this project:

-   Read request (0x10, len=0x06): asks for a single SID (last byte).
-   Read response (0x90): SID/VAL pairs in the payload.
-   Simple write (0x10, len=0x07): command + value (used for power, swing, p-select).
-   Extended write (0x10, len=0x0A): `F8` + `mode` + `setpoint` + `fan` + `other`.
-   Event notification (0x11): device event; we reply with 0x91 ACK.
    Model is updated directly from the event payload; periodic polling is the fallback.
-   Polling reads SIDs: 0x80, 0xB0, 0x90, 0xA3, 0x87, 0xBB, 0xBE.
-   Session recovery: if write commands get no response twice in a row, the driver waits 2s and re-handshakes.
-   Handshake note: HS3 is sent without waiting for a response (none is typically observed).

Parameters (hex values):

-   Power (SID 0x80): ON=0x30, OFF=0x31.
-   Swing (CMD 0xA3): ON=0x41, OFF=0x31.
-   Mode codes: AUTO=0x41, COOL=0x42, HEAT=0x43, DRY=0x44, FAN=0x45.
-   Fan codes: QUIET=0x31, 1=0x32, 2=0x33, 3=0x34, 4=0x35, 5=0x36, AUTO=0x41.
-   Setpoint: 0x14..0x1E (20..30 °C).
-   Other (F8 last byte from sniffed OEM module):
    OFF=0x00, HI_POWER=0x01, OUTDOOR_SILENT_ON=0x02, ECO=0x03, 8DEG=0x04,
    FLOOR=0x06, OUTDOOR_SILENT_MID=0x0A, HEAT_EXCHANGE_ON=0x20, HEAT_EXCHANGE_MID=0x30.
-   P-select (CMD 0x87): 50%=0x32, 75%=0x4B, 100%=0x64.
-   Read responses include SID 0xA3 (swing) and SID 0x87 (p-select).

## Notes

-   Requires `pymodbus` in the host Python environment (pip install pymodbus).
-   The project already includes MicroPython stubs in `src/lib/`, so installing them with `mip` is optional.
-   If you hit connection or read errors, verify IP, port, unit id and firewall settings.
