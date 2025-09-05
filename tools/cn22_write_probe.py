# cn22_write_probe.py — Minimal CN22 single-shot control/debug (ESP32 MicroPython)
# - 9600 8E1 on TX=32, RX=33
# - Handshake, optional read(s), exactly one write, print decoded ACKs / replies
#
# Updates vs older probe:
# - Swing writes: SIMPLE 0xA3 with 0x41 (ON) / 0x31 (OFF)
# - Fan writes: send (mode_code,temp) first; auto = (mode_code,temp) + keep-temp pair (no fan pair)
# - Read decoding: mode codes 0x41..0x45, fan codes 0x31..0x36 to friendly names
# - Avoid relying on READ 0x41 (often no reply). Use default temp or ARG.

import uasyncio as asyncio
import time
from machine import UART, Pin

# ========= Choose ONE test =========
# TEST options:
#   "read_all"           (reads 0x80, 0xB0, 0x90, 0xBB, 0xBE)
#   "read_sid"           (ARG = int sid, e.g. 0xBB)
#   "set_temp"           (ARG = temperature C, 16..30)
#   "power_on", "power_off"
#   "mode_auto","mode_cool","mode_heat","mode_dry","mode_fan"  (uses ARG temp or default)
#   "fan_auto","fan_quiet","fan_1","fan_2","fan_3","fan_4","fan_5" (uses ARG temp or default)
#   "swing_on","swing_off"
TEST = "set_temp"
ARG = 22  # used as temperature for set_temp/mode_*/fan_*; ignored for others

# ========= UART config =========
TX_PIN, RX_PIN, UART_ID = 32, 33, 1
BAUD, BITS, PARITY, STOP = 9600, 8, 0, 1  # EVEN parity on ESP32 MicroPython
RXBUF = 4096

# ========= timings =========
START_DELAY_MS = 200
WAIT_RX_MS = 700
GAP_SHORT_MS = 20
GAP_NORMAL_MS = 60

DEFAULT_TEMP_C = 22


# ========= helpers =========
def hx(b):
    return " ".join(f"{x:02X}" for x in b)


def chk_to_0x02(d):
    return (0x02 - (sum(d) & 0xFF)) & 0xFF


def clamp_temp(t):
    t = int(t)
    return 16 if t < 16 else 30 if t > 30 else t


async def rx_collect(u, ms):
    buf = bytearray()
    for _ in range(max(1, ms // 10)):
        await asyncio.sleep_ms(10)
        d = u.read()
        if d:
            buf.extend(d)
    return bytes(buf)


async def wait_idle(u, idle_ms=30, timeout_ms=1200):
    t0 = time.ticks_ms()
    last = t0
    while True:
        d = u.read()
        if d:
            last = time.ticks_ms()
        if time.ticks_diff(time.ticks_ms(), last) >= idle_ms:
            return True
        if time.ticks_diff(time.ticks_ms(), t0) >= timeout_ms:
            return False
        await asyncio.sleep_ms(4)


async def send(u, label, frame, wait_ms=WAIT_RX_MS, gap_ms=GAP_SHORT_MS):
    try:
        while u.read():
            pass
    except:
        pass
    await wait_idle(u, 30, 1200)
    print(f"TX {label} ({len(frame)}B):", hx(frame))
    u.write(frame)
    rsp = await rx_collect(u, wait_ms)
    if rsp:
        print(f"RX {label} ({len(rsp)}B):", hx(rsp))
    else:
        print(f"RX {label}: <no response>")
    await asyncio.sleep_ms(gap_ms)
    return rsp


# ========= 0x90 ACK parsing & decoding =========
def parse_0x90_frames(stream: bytes):
    out = []
    if not stream:
        return out
    i, n = 0, len(stream)
    while i + 8 <= n:
        if stream[i : i + 4] != b"\x02\x00\x03\x90":
            i += 1
            continue
        ln = stream[i + 6]
        total = 7 + ln + 1
        if i + total > n:
            break
        frame = stream[i : i + total]
        sid, val, chk = frame[-3], frame[-2], frame[-1]
        ok = ((sum(frame[:-1]) + chk) & 0xFF) == 0x02
        out.append((sid, val, ok, frame))
        i += total
    return out


MODE_CODE_NUM = {0: 0x41, 1: 0x42, 2: 0x43, 3: 0x44, 4: 0x45}
MODE_NAME_FROM_CODE = {
    0x41: "auto",
    0x42: "cool",
    0x43: "heat",
    0x44: "dry",
    0x45: "fan",
}
MODE_NAME_FROM_NUM = {0: "auto", 1: "cool", 2: "heat", 3: "dry", 4: "fan"}

FAN_CODE = {"quiet": 0x31, "1": 0x32, "2": 0x33, "3": 0x34, "4": 0x35, "5": 0x36}
FAN_NAME_FROM_CODE = {
    0x31: "quiet",
    0x32: "1",
    0x33: "2",
    0x34: "3",
    0x35: "4",
    0x36: "5",
}
FAN_NAME_FROM_NUM = {0: "quiet", 1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "auto"}

VAL_POWER_ON, VAL_POWER_OFF = 0x30, 0x31


def decode_sid_val(sid, val):
    if sid == 0x80:
        return "power=" + (
            "ON"
            if val == VAL_POWER_ON
            else "OFF" if val == VAL_POWER_OFF else f"0x{val:02X}"
        )
    if sid == 0xB0:
        if val in MODE_NAME_FROM_CODE:
            return f"mode={MODE_NAME_FROM_CODE[val]}"
        if val in MODE_NAME_FROM_NUM:
            return f"mode={MODE_NAME_FROM_NUM[val]}"
    if sid == 0x90:
        if val in FAN_NAME_FROM_CODE:
            return f"fan={FAN_NAME_FROM_CODE[val]}"
        if val in FAN_NAME_FROM_NUM:
            return f"fan={FAN_NAME_FROM_NUM[val]}"
    if sid == 0xBB:
        return f"room≈{val}°C"
    if sid == 0xBE:
        return f"outdoor≈{val}°C"
    return None


def print_acks(tag, data: bytes):
    for sid, val, ok, _ in parse_0x90_frames(data):
        if sid == 0x01:
            print(f"ACK[{tag}]  cmd=0x{val:02X}  chk={'OK' if ok else 'BAD'}")
        else:
            extra = decode_sid_val(sid, val)
            if extra:
                print(
                    f"ACK[{tag}]  sid=0x{sid:02X}  val=0x{val:02X} ({extra})  chk={'OK' if ok else 'BAD'}"
                )
            else:
                print(
                    f"ACK[{tag}]  sid=0x{sid:02X}  val=0x{val:02X}  chk={'OK' if ok else 'BAD'}"
                )


# ========= frames (sniff-confirmed) =========
HS = [
    bytes([0x02, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x02]),
    bytes([0x02, 0xFF, 0xFF, 0x01, 0x00, 0x00, 0x01, 0x02, 0xFE]),
    bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0x02, 0x02, 0xFA]),
    bytes([0x02, 0x00, 0x01, 0x81, 0x01, 0x00, 0x02, 0x00, 0x00, 0x7B]),
    bytes([0x02, 0x00, 0x01, 0x02, 0x00, 0x00, 0x02, 0x00, 0x00, 0xFB]),
    bytes([0x02, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0xFE]),
]
AF = [
    bytes([0x02, 0x00, 0x02, 0x01, 0x00, 0x00, 0x02, 0x00, 0x00, 0xFB]),
    bytes([0x02, 0x00, 0x02, 0x02, 0x00, 0x00, 0x02, 0x00, 0x00, 0xFA]),
]

BASE_READ = bytes(
    [0x02, 0x00, 0x03, 0x10, 0x00, 0x00, 0x06, 0x01, 0x30, 0x01, 0x00, 0x01]
)
BASE_SIMPLE = bytes(
    [0x02, 0x00, 0x03, 0x10, 0x00, 0x00, 0x07, 0x01, 0x30, 0x01, 0x00, 0x02]
)
BASE_EXT = bytes(
    [0x02, 0x00, 0x03, 0x10, 0x00, 0x00, 0x0A, 0x01, 0x30, 0x01, 0x00, 0x05]
)


def build_read(sid):
    body = BASE_READ + bytes([sid & 0xFF])
    return body + bytes([chk_to_0x02(body)])


def build_simple(cmd, val):
    body = BASE_SIMPLE + bytes([cmd & 0xFF, val & 0xFF])
    return body + bytes([chk_to_0x02(body)])


def build_ext_pairs(pairs):
    body = (
        BASE_EXT + b"\xf8" + b"".join(bytes([r & 0xFF, v & 0xFF]) for (r, v) in pairs)
    )
    return body + bytes([chk_to_0x02(body)])


# set temp (EXT)
def build_set_temp(temp_c):
    t = clamp_temp(temp_c)
    return build_ext_pairs([(0x41, t), (0x41, 0x00)])  # F8 41 <t> 41 00


# mode (EXT)
MODE_CODE_BY_NAME = {"auto": 0x41, "cool": 0x42, "heat": 0x43, "dry": 0x44, "fan": 0x45}


def build_set_mode_ext(temp_c, mode_name):
    code = MODE_CODE_BY_NAME[mode_name]
    t = clamp_temp(temp_c)
    return build_ext_pairs([(code, t), (0x41, 0x00)])


# fan (EXT) with mode-first
def build_set_fan_ext_with_mode(temp_c, level_name, mode_code):
    t = clamp_temp(temp_c)
    if level_name == "auto":
        # auto = only mode+temp + keep-temp pair (no fan pair)
        return build_ext_pairs([(mode_code, t), (0x41, 0x00)])
    code = FAN_CODE[level_name]
    return build_ext_pairs([(mode_code, t), (code, 0x00)])


# simple command IDs (confirmed)
CMD_POWER = 0x80
CMD_SWING = 0xA3  # swing: 0x41 ON / 0x31 OFF


# ===== helper: get current mode code (reads 0xB0; falls back to 0x41) =====
async def get_current_mode_code(u):
    r = await send(u, "READ mode(0xB0)", build_read(0xB0), wait_ms=800)
    print_acks("READ mode", r)
    code = 0x41  # default to 'auto'
    for sid, val, ok, _ in parse_0x90_frames(r):
        if ok and sid == 0xB0:
            if val in MODE_NAME_FROM_CODE:
                return val
            if val in MODE_NAME_FROM_NUM:
                idx = val
                return MODE_CODE_NUM.get(idx, 0x41)
    return code


async def main():
    print(f"CN22 probe @ {BAUD} 8E1  TX={TX_PIN}  RX={RX_PIN}  TEST={TEST} ARG={ARG}")
    u = UART(
        UART_ID,
        baudrate=BAUD,
        bits=BITS,
        parity=PARITY,
        stop=STOP,
        tx=Pin(TX_PIN),
        rx=Pin(RX_PIN),
        rxbuf=RXBUF,
    )
    try:
        await asyncio.sleep_ms(START_DELAY_MS)
        # Handshake
        for i, f in enumerate(HS):
            r = await send(u, f"HS{i}", f, wait_ms=500, gap_ms=GAP_NORMAL_MS)
            print_acks(f"HS{i}", r)
        for i, f in enumerate(AF):
            r = await send(u, f"AF{i}", f, wait_ms=500, gap_ms=GAP_NORMAL_MS)
            print_acks(f"AF{i}", r)

        # quick helper to read a SID
        async def read_sid(sid, label=None):
            r = await send(u, f"READ {label or hex(sid)}", build_read(sid), wait_ms=800)
            print_acks(f"READ {label or hex(sid)}", r)
            return r

        # choose temp for writes (don't rely on READ 0x41)
        chosen_t = clamp_temp(ARG if isinstance(ARG, int) else DEFAULT_TEMP_C)

        # Execute one test
        if TEST == "read_all":
            for sid, label in [
                (0x80, "power"),
                (0xB0, "mode"),
                (0x90, "fan"),
                (0xBB, "room"),
                (0xBE, "outdoor"),
            ]:
                await read_sid(sid, label)
            return

        if TEST == "read_sid":
            await read_sid(int(ARG) & 0xFF)
            return

        if TEST == "set_temp":
            pkt = build_set_temp(chosen_t)
            r = await send(
                u, f"SET_TEMP {chosen_t}C", pkt, wait_ms=800, gap_ms=GAP_NORMAL_MS
            )
            print_acks("SET_TEMP", r)
            # Many units don't reply to READ 0x41 → skip verify to avoid delay.
            return

        if TEST in ("power_on", "power_off"):
            val = VAL_POWER_ON if TEST.endswith("on") else VAL_POWER_OFF
            r = await send(
                u,
                f"POWER {'ON' if val==VAL_POWER_ON else 'OFF'}",
                build_simple(CMD_POWER, val),
                wait_ms=800,
                gap_ms=GAP_NORMAL_MS,
            )
            print_acks("POWER", r)
            await read_sid(0x80, "power(verify)")
            if val == VAL_POWER_OFF:
                # OEM behavior: send keep-temp after OFF
                r2 = await send(
                    u,
                    "SET_TEMP keep",
                    build_set_temp(chosen_t),
                    wait_ms=800,
                    gap_ms=GAP_NORMAL_MS,
                )
                print_acks("SET_TEMP keep", r2)
            return

        if TEST.startswith("mode_"):
            name = TEST.split("_", 1)[1]
            if name not in MODE_CODE_BY_NAME:
                print("Bad mode")
                return
            pkt = build_set_mode_ext(chosen_t, name)
            r = await send(
                u,
                f"MODE {name} (EXT, {chosen_t}C)",
                pkt,
                wait_ms=800,
                gap_ms=GAP_NORMAL_MS,
            )
            print_acks("MODE_EXT", r)
            await read_sid(0xB0, "mode(verify)")
            return

        if TEST.startswith("fan_"):
            name = TEST.split("_", 1)[1]
            if name not in ("auto", "quiet", "1", "2", "3", "4", "5"):
                print("Bad fan")
                return
            mode_code = await get_current_mode_code(u)
            pkt = build_set_fan_ext_with_mode(chosen_t, name, mode_code)
            r = await send(
                u,
                f"FAN {name} (EXT, {chosen_t}C)",
                pkt,
                wait_ms=800,
                gap_ms=GAP_NORMAL_MS,
            )
            print_acks("FAN_EXT", r)
            await read_sid(0x90, "fan(verify)")
            return

        if TEST in ("swing_on", "swing_off"):
            on = TEST.endswith("on")
            val = 0x41 if on else 0x31
            r = await send(
                u,
                f"SWING {'ON' if on else 'OFF'}",
                build_simple(CMD_SWING, val),
                wait_ms=800,
                gap_ms=GAP_NORMAL_MS,
            )
            print_acks(
                "SWING", r
            )  # expect 0x90 ... 01 A3 echo; no readable swing SID seen
            return

        print("Unknown TEST option:", TEST)

    finally:
        try:
            u.deinit()
        except:
            pass


asyncio.run(main())
