#!/usr/bin/env python3
import argparse
import time
from pymodbus.client import ModbusTcpClient

labels = [
    "state",
    "mode",
    "fanmode",
    "swing",
    "setpoint_x10",
    "roomtemp_x10",
    "outdoort_x10",
    "rssi_dbm",
]

# decode tables
modes = {0: "AUTO", 1: "COOL", 2: "HEAT", 3: "DRY", 4: "FAN"}
fanmodes = {0: "QUIET", 1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "AUTO"}


# ---- compatibility helper for pymodbus kwarg name (unit/slave/device_id) ----
def _call_with_unit(func, *args, unit=None, **kwargs):
    last_exc = None
    for key in ("unit", "slave", "device_id"):
        try:
            if unit is None:
                return func(*args, **kwargs)
            return func(*args, **kwargs, **{key: unit})
        except TypeError as e:
            last_exc = e
            continue
    # If all variants failed, re-raise the last TypeError
    raise last_exc


def decode_and_print(registers):
    for i, val in enumerate(registers):
        label = labels[i] if i < len(labels) else f"reg{i}"

        if label == "mode":
            print(f"{label}: {val} ({modes.get(val, 'UNKNOWN')})")
        elif label == "fanmode":
            print(f"{label}: {val} ({fanmodes.get(val, 'UNKNOWN')})")
        elif label == "state":
            print(f"{label}: {val} ({'ON' if val == 1 else 'OFF'})")
        elif label == "swing":
            print(f"{label}: {val} ({'ON' if val == 1 else 'OFF'})")
        elif label in ("setpoint_x10", "roomtemp_x10", "outdoort_x10"):
            if val >= 0x8000:
                val -= 0x10000
            print(f"{label}: {val/10.0:.1f} °C")
        elif label == "rssi_dbm":
            if val >= 0x8000:
                val -= 0x10000
            print(f"{label}: {val} dBm")
        else:
            print(f"{label}: {val}")


def read_status(client, unit=1, address=0, count=8):
    rr = _call_with_unit(
        client.read_holding_registers, address=address, count=count, unit=unit
    )
    if rr.isError():
        print("Read error:", rr)
        return None
    regs = rr.registers
    decode_and_print(regs)
    return regs


# ---- write helpers (all go through _call_with_unit) ----
def write_state(client, val, unit=1):
    return _call_with_unit(client.write_register, 0, 1 if int(val) else 0, unit=unit)


def write_mode(client, val, unit=1):
    return _call_with_unit(client.write_register, 1, int(val), unit=unit)


def write_fanmode(client, val, unit=1):
    return _call_with_unit(client.write_register, 2, int(val), unit=unit)


def write_swing(client, val, unit=1):
    return _call_with_unit(client.write_register, 3, 1 if int(val) else 0, unit=unit)


def write_setpoint(client, val, unit=1):
    celsius = float(val)
    regval = int(round(celsius * 10))
    return _call_with_unit(client.write_register, 4, regval, unit=unit)


WRITE_FUNCS = {
    "state": write_state,
    "mode": write_mode,
    "fanmode": write_fanmode,
    "swing": write_swing,
    "setpoint": write_setpoint,
}


def parse_args():
    p = argparse.ArgumentParser(
        description="Poll and control a Modbus TCP HVAC device."
    )
    p.add_argument("ip", help="Modbus TCP server IP (e.g., 192.168.94.203)")
    p.add_argument(
        "--port", type=int, default=502, help="Modbus TCP port (default: 502)"
    )
    p.add_argument(
        "--unit", type=int, default=1, help="Modbus unit/slave id (default: 1)"
    )
    p.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Polling interval seconds (default: 2)",
    )
    p.add_argument(
        "--address",
        type=int,
        default=0,
        help="Start address for status read (default: 0)",
    )
    p.add_argument(
        "--count", type=int, default=8, help="Number of registers to read (default: 8)"
    )
    p.add_argument("--once", action="store_true", help="Read status once and exit")

    # write options
    p.add_argument(
        "--write-func", choices=WRITE_FUNCS.keys(), help="Which value to write"
    )
    p.add_argument("--value", help="Value to write (required with --write-func)")
    return p.parse_args()


def main():
    args = parse_args()
    print(f"Connecting to {args.ip}:{args.port} (unit {args.unit}) ...")

    with ModbusTcpClient(args.ip, port=args.port) as client:
        if not client.connect():
            print("Failed to connect.")
            return

        try:
            if args.write_func:
                if args.value is None:
                    print("Error: --value is required with --write-func")
                    return
                func = WRITE_FUNCS[args.write_func]
                result = func(client, args.value, unit=args.unit)
                print(f"Wrote {args.write_func}={args.value}, result={result}")
                return

            # polling mode
            while True:
                print("---------------------")
                read_status(
                    client, unit=args.unit, address=args.address, count=args.count
                )
                if args.once:
                    break
                time.sleep(args.interval)

        except KeyboardInterrupt:
            print("\nReceived Ctrl+C, shutting down gracefully...")
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
