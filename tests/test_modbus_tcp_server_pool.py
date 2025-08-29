from pymodbus.client import ModbusTcpClient
import random
import time

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


def decode_and_print(registers):
    for i, val in enumerate(registers):
        label = labels[i] if i < len(labels) else f"reg{i}"

        if label == "mode":
            print(f"{label}: {val} ({modes.get(val, 'UNKNOWN')})")
        elif label == "fanmode":
            print(f"{label}: {val} ({fanmodes.get(val, 'UNKNOWN')})")
        elif label == "state":
            print(f"{label}: {val} ({'ON' if val==1 else 'OFF'})")
        elif label == "swing":
            print(f"{label}: {val} ({'ON' if val==1 else 'OFF'})")
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


def read_status(client, unit=1):
    rr = client.read_holding_registers(0, count=8, device_id=unit)
    if rr.isError():
        print("Read error:", rr)
        return None
    regs = rr.registers
    decode_and_print(regs)
    return regs


def write_state(client, on, unit=1):
    val = 1 if on else 0
    return client.write_register(0, val, device_id=unit)


def write_mode(client, mode, unit=1):
    return client.write_register(1, mode, device_id=unit)


def write_fanmode(client, fan, unit=1):
    return client.write_register(2, fan, device_id=unit)


def write_swing(client, on, unit=1):
    val = 1 if on else 0
    return client.write_register(3, val, device_id=unit)


def write_setpoint(client, temp_c, unit=1):
    val = int(round(temp_c * 10))
    return client.write_register(4, val, device_id=unit)


# -------------------------
# Example usage
# -------------------------
IP = "192.168.94.252"
c = ModbusTcpClient(IP, port=502)
c.connect()

""" print("Initial status:") """
while True:
    print("---------------------")
    read_status(c)
    time.sleep(1)

""" write_state(c, random.choice([True, False]))
write_mode(c, random.randint(0, 4))
write_fanmode(c, random.randint(0, 6))
write_swing(c, random.choice([True, False]))
write_setpoint(c, random.uniform(20.0, 24.0))

print("\nNew status:")
read_status(c) """

c.close()
