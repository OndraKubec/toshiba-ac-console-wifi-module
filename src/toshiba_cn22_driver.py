# toshiba_cn22_driver.py — Toshiba CN22 UART driver (ESP32 MicroPython, uasyncio)
# Pins (your wiring):
#   TX=32 -> CN22 pin 1 (via 3v3→5V level shifter)
#   RX=33 <- CN22 pin 4 (via 5V→3v3 level shifter)
#
# Features:
# - Handshake + AFTER_HS keepalive (sniff-confirmed frames)
# - Periodic polling of key SIDs (power/mode/fan/temps)  [0x41 omitted: often no reply]
# - Writes:
#     • setpoint:      EXT  F8 <mode_code> <temp> 41 00   (mode-first → no mode flip)
#     • mode:          EXT  F8 <mode_code> <temp> 41 00   (auto/cool/heat/dry/fan)
#     • fan (quiet..5):EXT  F8 <mode_code> <temp> <fan_code> 00  (mode-first → no mode flip)
#       fan auto:      EXT  F8 <mode_code> <temp> 41 00   (no explicit fan code seen yet)
#     • swing:         SIMPLE 0xA3 0x41(on) / 0x31(off)   (from sniff)
# - Reflects 0x90 replies into ACModel fields
#   • mode SID(0xB0) returns code 0x41..0x45 → decoded to 0..4
#   • fan  SID(0x90) accepts 0..6 OR codes 0x31..0x36
#   • swing has no readable SID; model.swing synced from writes
# - Optional 0x11 event watcher (ACK with 0x91 + burst poll)
# - Debug logging: set debug=1 (TX/RX, timings) or debug=2 (include hex dumps)

import uasyncio as asyncio
import time
import config
from machine import UART, Pin

# ========================= UART & TIMING =========================
TX_PIN, RX_PIN, UART_ID = config.UART_TX_PIN, config.UART_RX_PIN, 1
BAUD, BITS, PARITY, STOP = 9600, 8, 0, 1  # parity=0 => EVEN on ESP32 MicroPython
RXBUF = 4096

START_DELAY_MS = 200
WAIT_RX_MS = 700
POLL_WAIT_MS = 900
GAP_SHORT_MS = 20
GAP_NORMAL_MS = 60
KEEPALIVE_EVERY_MS = 60000
REHANDSHAKE_MISSED = 2

# ========================= COMMAND IDS / MAPS ====================
CMD_POWER = 0x80
CMD_MODE = 0xB0
CMD_FAN = 0x90
# Swing per sniff: SIMPLE 0xA3 with 0x41 (ON) / 0x31 (OFF)
CMD_SWING = 0xA3
VAL_SWING_ON, VAL_SWING_OFF = 0x41, 0x31

VAL_POWER_ON, VAL_POWER_OFF = 0x30, 0x31

# Mode (EXT) — from sniffs: F8 <code> <temp> 41 00
# ACModel order: 0=auto,1=cool,2=heat,3=dry,4=fan
MODE_CODE = {0: 0x41, 1: 0x42, 2: 0x43, 3: 0x44, 4: 0x45}
MODE_CODE_REV = {v: k for k, v in MODE_CODE.items()}

# Fan (EXT) — from sniffs:
# quiet:0x31, 1:0x32, 2:0x33, 3:0x34, 4:0x35, 5:0x36
FAN_CODE = {0: 0x31, 1: 0x32, 2: 0x33, 3: 0x34, 4: 0x35, 5: 0x36}
FAN_CODE_REV = {v: k for k, v in FAN_CODE.items()}
# Fan auto had no distinct code in sniffs; using mode+temp only (no fan pair)
FAN_AUTO_SENT_AS_MODE_TEMP_ONLY = True


# ========================= PROTOCOL HELPERS ======================
def _chk_to_0x02(d):
    return (0x02 - (sum(d) & 0xFF)) & 0xFF


BASE_READ = bytes(
    [0x02, 0x00, 0x03, 0x10, 0x00, 0x00, 0x06, 0x01, 0x30, 0x01, 0x00, 0x01]
)
BASE_SIMPLE = bytes(
    [0x02, 0x00, 0x03, 0x10, 0x00, 0x00, 0x07, 0x01, 0x30, 0x01, 0x00, 0x02]
)
BASE_EXT = bytes(
    [0x02, 0x00, 0x03, 0x10, 0x00, 0x00, 0x0A, 0x01, 0x30, 0x01, 0x00, 0x05]
)


def _build_read(sid):
    body = BASE_READ + bytes([sid & 0xFF])
    return body + bytes([_chk_to_0x02(body)])


def _build_simple(cmd, val):
    body = BASE_SIMPLE + bytes([cmd & 0xFF, val & 0xFF])
    return body + bytes([_chk_to_0x02(body)])


def _build_ext_pairs(pairs):
    # pairs = [(reg,val), ...]
    body = (
        BASE_EXT + b"\xf8" + b"".join(bytes([r & 0xFF, v & 0xFF]) for (r, v) in pairs)
    )
    return body + bytes([_chk_to_0x02(body)])


def _clamp_temp(t):
    return 16 if t < 16 else 30 if t > 30 else t


def _build_set_mode_ext(temp_c, mode):
    code = MODE_CODE.get(mode, 0x41)
    t = _clamp_temp(temp_c)
    return _build_ext_pairs([(code, t), (0x41, 0x00)])


def _build_set_temp_keep_mode(temp_c, mode_code):
    """Set temperature WITHOUT flipping mode: send <mode_code, temp> + keep-temp."""
    t = _clamp_temp(temp_c)
    return _build_ext_pairs([(mode_code, t), (0x41, 0x00)])


def _build_set_fan_ext_with_mode(temp_c, fan, mode_code):
    # To prevent mode jumping to AUTO, send the current mode first.
    # fan auto → no fan pair (only mode+temp), based on sniffs.
    t = _clamp_temp(temp_c)
    if FAN_AUTO_SENT_AS_MODE_TEMP_ONLY and fan == 6:
        return _build_ext_pairs([(mode_code, t), (0x41, 0x00)])
    code = FAN_CODE.get(fan, 0x32)  # default to step 1 if out of range
    return _build_ext_pairs([(mode_code, t), (code, 0x00)])


def _parse_0x90_frames(stream):
    """Return [(sid, val, ok), ...] parsed from any 0x90 frames in stream."""
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
        fr = stream[i : i + total]
        sid, val, chk = fr[-3], fr[-2], fr[-1]
        ok = ((sum(fr[:-1]) + chk) & 0xFF) == 0x02
        out.append((sid, val, ok))
        i += total
    return out


def _build_ack_91():
    # ACK for 0x11 events (matches OEM):
    # 02 00 03 91 01 00 05  00 00 01 30 01  <chk>
    body = bytes(
        [0x02, 0x00, 0x03, 0x91, 0x01, 0x00, 0x05, 0x00, 0x00, 0x01, 0x30, 0x01]
    )
    return body + bytes([_chk_to_0x02(body)])


# ========================= HANDSHAKE (sniffed) ===================
HS = [
    bytes([0x02, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x02]),
    bytes([0x02, 0xFF, 0xFF, 0x01, 0x00, 0x00, 0x01, 0x02, 0xFE]),
    bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0x02, 0x02, 0xFA]),
    bytes([0x02, 0x00, 0x01, 0x81, 0x01, 0x00, 0x02, 0x00, 0x00, 0x7B]),
    bytes([0x02, 0x00, 0x01, 0x02, 0x00, 0x00, 0x02, 0x00, 0x00, 0xFB]),
    bytes([0x02, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0xFE]),
]
AFTER_HS = [
    bytes([0x02, 0x00, 0x02, 0x01, 0x00, 0x00, 0x02, 0x00, 0x00, 0xFB]),
    bytes([0x02, 0x00, 0x02, 0x02, 0x00, 0x00, 0x02, 0x00, 0x00, 0xFA]),
]


# ========================= DRIVER ================================
class ToshibaCN22Driver:
    def __init__(self, model, tx_pin=TX_PIN, rx_pin=RX_PIN, uart_id=UART_ID, debug=0):
        self.model = model
        self.tx_pin = tx_pin
        self.rx_pin = rx_pin
        self.uart_id = uart_id
        self.uart = None
        self._handshaked = False
        self._missed_keepalive = 0
        self._lock = asyncio.Lock()
        self._busy = False
        self._event_polling = False
        self.debug = int(debug)  # 0: off, 1: basic, 2: hex

    # ---- debug helpers ----
    def set_debug(self, level):
        self.debug = int(level)

    def _now_ms(self):
        try:
            return time.ticks_ms()
        except:
            return 0

    def _log(self, *args):
        if self.debug:
            print("[%d] " % self._now_ms(), *args)

    def _hex(self, b):
        try:
            return " ".join("%02X" % x for x in b)
        except:
            return str(b)

    # ---- low-level I/O ----
    def _mk_uart(self):
        self._log(
            "UART init: id=%d tx=%d rx=%d baud=%d bits=%d parity=%s stop=%d rxbuf=%d"
            % (self.uart_id, self.tx_pin, self.rx_pin, BAUD, BITS, "EVEN", STOP, RXBUF)
        )
        return UART(
            self.uart_id,
            baudrate=BAUD,
            bits=BITS,
            parity=PARITY,
            stop=STOP,
            tx=Pin(self.tx_pin),
            rx=Pin(self.rx_pin),
            rxbuf=RXBUF,
        )

    async def _rx_collect(self, ms):
        buf = bytearray()
        chunks = 0
        for _ in range(max(1, ms // 10)):
            await asyncio.sleep_ms(10)
            try:
                d = self.uart.read()
            except Exception as e:
                self._log("UART read error:", e)
                d = None
            if d:
                chunks += 1
                buf.extend(d)
        if self.debug >= 1:
            self._log("RX collect: %d ms, %d chunks, %d bytes" % (ms, chunks, len(buf)))
            if self.debug >= 2 and buf:
                self._log("RX hex:", self._hex(buf))
        return bytes(buf)

    async def _wait_idle(self, idle_ms=30, timeout_ms=1200):
        t0 = time.ticks_ms()
        last = t0
        while True:
            try:
                d = self.uart.read()
            except Exception as e:
                self._log("UART read error during wait_idle:", e)
                d = None
            if d:
                last = time.ticks_ms()
            if time.ticks_diff(time.ticks_ms(), last) >= idle_ms:
                if self.debug >= 1:
                    self._log("wait_idle: idle for", idle_ms, "ms")
                return True
            if time.ticks_diff(time.ticks_ms(), t0) >= timeout_ms:
                if self.debug >= 1:
                    self._log("wait_idle: TIMEOUT after", timeout_ms, "ms")
                return False
            await asyncio.sleep_ms(4)

    async def _send(
        self, label, frame, wait_ms=WAIT_RX_MS, gap_ms=GAP_SHORT_MS, verbose=False
    ):
        self._busy = True
        try:
            try:
                while True:
                    d = self.uart.read()
                    if not d:
                        break
            except Exception as e:
                self._log("UART drain error:", e)
            idle_ok = await self._wait_idle(30, 1200)
            if self.debug >= 1:
                self._log("TX %s len=%d idle_ok=%s" % (label, len(frame), idle_ok))
                if self.debug >= 2:
                    self._log("TX hex:", self._hex(frame))
            self.uart.write(frame)
            rsp = await self._rx_collect(wait_ms)
            if self.debug >= 1:
                if rsp:
                    self._log("RX %s len=%d" % (label, len(rsp)))
                else:
                    self._log("RX %s: <no response>" % label)
            await asyncio.sleep_ms(gap_ms)
            self._reflect_stream(rsp)
            return rsp
        finally:
            self._busy = False

    # ---- session maintenance ----
    async def handshake(self):
        async with self._lock:
            if self.uart is None:
                self.uart = self._mk_uart()
            self._handshaked = False
            self._log("Handshake: start (delay %d ms)" % START_DELAY_MS)
            await asyncio.sleep_ms(START_DELAY_MS)
            try:
                await self._send(
                    "HS0", HS[0], gap_ms=GAP_NORMAL_MS, verbose=(self.debug >= 1)
                )
                await self._send(
                    "HS1", HS[1], gap_ms=GAP_SHORT_MS, verbose=(self.debug >= 1)
                )
                await self._send(
                    "HS2", HS[2], gap_ms=GAP_NORMAL_MS, verbose=(self.debug >= 1)
                )
                await self._send(
                    "HS3", HS[3], gap_ms=GAP_SHORT_MS, verbose=(self.debug >= 1)
                )
                await self._send(
                    "HS4", HS[4], gap_ms=GAP_SHORT_MS, verbose=(self.debug >= 1)
                )
                await self._send(
                    "HS5", HS[5], gap_ms=GAP_NORMAL_MS, verbose=(self.debug >= 1)
                )
                await self._send(
                    "AF0", AFTER_HS[0], gap_ms=GAP_SHORT_MS, verbose=(self.debug >= 1)
                )
                await self._send(
                    "AF1", AFTER_HS[1], gap_ms=GAP_NORMAL_MS, verbose=(self.debug >= 1)
                )
                self._handshaked = True
                self._missed_keepalive = 0
                self._log("Handshake: DONE")
            except Exception as e:
                self._log("Handshake error:", e)
                raise

    async def keepalive_task(self):
        while True:
            try:
                if not self._handshaked:
                    self._log("keepalive: not handshaked -> handshake()")
                    await self.handshake()
                    await asyncio.sleep_ms(1000)
                    continue
                ok = False
                for i, fr in enumerate(AFTER_HS):
                    rsp = await self._send(
                        "KA%d" % i,
                        fr,
                        wait_ms=300,
                        gap_ms=GAP_SHORT_MS,
                        verbose=(self.debug >= 1),
                    )
                    if rsp:
                        ok = True
                if not ok:
                    self._missed_keepalive += 1
                    self._log("keepalive: missed (%d)" % self._missed_keepalive)
                    if self._missed_keepalive >= REHANDSHAKE_MISSED:
                        self._log("keepalive: re-handshake")
                        self._handshaked = False
                        self._missed_keepalive = 0
                else:
                    if self._missed_keepalive:
                        self._log("keepalive: recovered")
                    self._missed_keepalive = 0
                await asyncio.sleep_ms(KEEPALIVE_EVERY_MS)
            except Exception as e:
                self._log("keepalive_task error:", e)
                await asyncio.sleep_ms(500)

    # ---- reflect replies into model ----
    def _reflect_sid_val(self, sid, val):
        if self.debug >= 2:
            self._log("reflect SID=0x%02X VAL=0x%02X" % (sid, val))
        if sid == 0x80:
            self.model.state = 1 if val == VAL_POWER_ON else 0
        elif sid == 0xB0:
            if val in MODE_CODE_REV:
                self.model.mode = MODE_CODE_REV[val]
            elif 0 <= val <= 4:
                self.model.mode = val
        elif sid == 0x90:
            if val in FAN_CODE_REV:
                self.model.fanmode = FAN_CODE_REV[val]
            elif 0 <= val <= 6:
                self.model.fanmode = val
        elif sid == 0x41:
            self.model.setpoint_x10 = int(val) * 10
        elif sid == 0xBB:
            self.model.roomtemp_x10 = int(val) * 10
        elif sid == 0xBE:
            self.model.outdoor_x10 = int(val) * 10

    def _reflect_stream(self, rsp):
        if not rsp:
            return
        frames = _parse_0x90_frames(rsp)
        if self.debug >= 1 and frames:
            self._log("reflect_stream: %d x 0x90 frames" % len(frames))
        for sid, val, ok in frames:
            if ok:
                self._reflect_sid_val(sid, val)
            elif self.debug >= 1:
                self._log("reflect: BAD CHK for SID=0x%02X VAL=0x%02X" % (sid, val))

    # ---- quick helper: get current mode_code (read if unknown) ----
    async def _get_current_mode_code(self):
        cur_mode = getattr(self.model, "mode", None)
        if cur_mode is not None and 0 <= int(cur_mode) <= 4:
            return MODE_CODE.get(int(cur_mode), 0x41)
        # Try to read SID 0xB0 once
        try:
            rsp = await self._send(
                "READB0(cur_mode)",
                _build_read(0xB0),
                wait_ms=POLL_WAIT_MS,
                gap_ms=GAP_SHORT_MS,
            )
            for sid, val, ok in _parse_0x90_frames(rsp):
                if ok and sid == 0xB0:
                    if val in MODE_CODE_REV:
                        self.model.mode = MODE_CODE_REV[val]
                        return val
                    elif 0 <= val <= 4:
                        self.model.mode = val
                        return MODE_CODE[val]
        except Exception as e:
            if self.debug:
                self._log("get_current_mode_code read failed:", e)
        # Fallback: assume AUTO
        return 0x41

    # ---- polling task ----
    async def poll_task(self, every_ms=2000):
        _POLL_SIDS = (0x80, 0xB0, 0x90, 0xBB, 0xBE)  # 0x41 omitted; swing omitted
        while True:
            try:
                if not self._handshaked:
                    await asyncio.sleep_ms(250)
                    continue
                for sid in _POLL_SIDS:
                    if self.debug >= 1:
                        self._log("poll: READ 0x%02X" % sid)
                    req = _build_read(sid)
                    _ = await self._send(
                        f"READ{sid:02X}",
                        req,
                        wait_ms=POLL_WAIT_MS,
                        verbose=(self.debug >= 1),
                    )
                await asyncio.sleep_ms(every_ms)
            except Exception as e:
                self._log("poll_task error:", e)
                await asyncio.sleep_ms(500)

    # ---- event watch (optional; start task from your app) ----
    def _feed_frames(self, buf):
        frames = []
        i = 0
        n = len(buf)
        while True:
            j = buf.find(b"\x02\x00\x03", i)
            if j < 0 or j + 7 > n:
                break
            ln = buf[j + 6]
            total = 7 + ln + 1
            if j + total > n:
                break
            frames.append(bytes(buf[j : j + total]))
            i = j + total
        if i > 0:
            buf[:] = buf[i:]
        elif len(buf) > 1024:
            buf[:] = buf[-64:]
        return frames

    async def _burst_poll(self):
        if self._event_polling:
            return
        self._event_polling = True
        try:
            for sid in (0x41, 0x80, 0xB0, 0x90, 0xBB, 0xBE):  # keep 0x41 just in case
                if self.debug >= 1:
                    self._log("burst_poll: READ 0x%02X" % sid)
                await self._send(
                    f"EVT_READ{sid:02X}",
                    _build_read(sid),
                    wait_ms=POLL_WAIT_MS,
                    verbose=(self.debug >= 1),
                )
                await asyncio.sleep_ms(20)
        finally:
            await asyncio.sleep_ms(200)
            self._event_polling = False

    async def event_watch_task(self):
        buf = bytearray()
        while True:
            try:
                if not self._handshaked or self.uart is None:
                    await asyncio.sleep_ms(200)
                    continue
                if self._busy:
                    await asyncio.sleep_ms(5)
                    continue
                try:
                    d = self.uart.read()
                except Exception as e:
                    self._log("UART read error in event_watch:", e)
                    d = None
                if d:
                    buf.extend(d)
                frames = self._feed_frames(buf)
                if frames and self.debug >= 1:
                    self._log("event_watch: %d frames" % len(frames))
                for fr in frames:
                    if len(fr) >= 7 and fr[:3] == b"\x02\x00\x03":
                        typ = fr[3]
                        if self.debug >= 2:
                            self._log(
                                "EVT frame typ=0x%02X len=%d hex=%s"
                                % (typ, len(fr), self._hex(fr))
                            )
                        if typ == 0x90:
                            self._reflect_stream(fr)
                        elif typ == 0x11:
                            try:
                                if self.debug >= 1:
                                    self._log("EVT: 0x11 -> send 0x91 ACK + burst_poll")
                                self.uart.write(_build_ack_91())
                            except Exception as e:
                                self._log("ACK 0x91 write error:", e)
                            asyncio.create_task(self._burst_poll())
                await asyncio.sleep_ms(2)
            except Exception as e:
                self._log("event_watch_task error:", e)
                await asyncio.sleep_ms(200)

    # ==================== PUBLIC API ====================
    async def set_setpoint_x10(self, sp_x10):
        t = _clamp_temp(sp_x10 // 10)
        if not self._handshaked:
            await self.handshake()
        mode_code = await self._get_current_mode_code()
        pkt = _build_set_temp_keep_mode(t, mode_code)
        await self._send(
            f"SET_TEMP({t}) keep-mode",
            pkt,
            wait_ms=800,
            gap_ms=GAP_NORMAL_MS,
            verbose=(self.debug >= 1),
        )
        self.model.setpoint_x10 = t * 10

    async def set_power(self, on):
        if not self._handshaked:
            await self.handshake()
        if on:
            await self._send(
                "POWER ON",
                _build_simple(CMD_POWER, VAL_POWER_ON),
                wait_ms=800,
                gap_ms=GAP_NORMAL_MS,
                verbose=(self.debug >= 1),
            )
        else:
            await self._send(
                "POWER OFF",
                _build_simple(CMD_POWER, VAL_POWER_OFF),
                wait_ms=800,
                gap_ms=GAP_NORMAL_MS,
                verbose=(self.debug >= 1),
            )
            # OEM behavior: send keep-temp after OFF, but do not flip mode
            temp_c = _clamp_temp(self.model.setpoint_x10 // 10)
            mode_code = await self._get_current_mode_code()
            await self._send(
                "SET_TEMP keep-mode after OFF",
                _build_set_temp_keep_mode(temp_c, mode_code),
                wait_ms=800,
                gap_ms=GAP_NORMAL_MS,
                verbose=(self.debug >= 1),
            )
        self.model.state = 1 if on else 0

    async def set_mode(self, mode):
        m = 0 if mode < 0 else 4 if mode > 4 else mode
        if not self._handshaked:
            await self.handshake()
        temp_c = _clamp_temp(self.model.setpoint_x10 // 10)
        pkt = _build_set_mode_ext(temp_c, m)
        await self._send(
            f"MODE_EXT({m})",
            pkt,
            wait_ms=800,
            gap_ms=GAP_NORMAL_MS,
            verbose=(self.debug >= 1),
        )
        self.model.mode = m

    async def set_fanmode(self, fan):
        """fan: 0=quiet,1..5=steps,6=auto."""
        f = 0 if fan < 0 else 6 if fan > 6 else fan
        if not self._handshaked:
            await self.handshake()
        temp_c = _clamp_temp(self.model.setpoint_x10 // 10)
        # Use current mode code to avoid mode flip
        cur_mode = getattr(self.model, "mode", None)
        mode_code = (
            MODE_CODE.get(int(cur_mode), 0x41)
            if cur_mode is not None
            else await self._get_current_mode_code()
        )
        pkt = _build_set_fan_ext_with_mode(temp_c, f, mode_code)
        await self._send(
            f"FAN_EXT({f})",
            pkt,
            wait_ms=800,
            gap_ms=GAP_NORMAL_MS,
            verbose=(self.debug >= 1),
        )
        self.model.fanmode = f

    async def set_swing(self, on):
        """Swing control from sniffs: SIMPLE 0xA3 with 0x41 (ON) / 0x31 (OFF)."""
        v = 1 if on else 0
        if not self._handshaked:
            await self.handshake()
        val = VAL_SWING_ON if on else VAL_SWING_OFF
        await self._send(
            f"SWING({'on' if on else 'off'})",
            _build_simple(CMD_SWING, val),
            wait_ms=800,
            gap_ms=GAP_NORMAL_MS,
            verbose=(self.debug >= 1),
        )
        self.model.swing = v  # no distinct swing SID observed

    async def close(self):
        if self.uart:
            try:
                self.uart.deinit()
            except Exception as e:
                self._log("UART deinit error:", e)
            self.uart = None
            self._handshaked = False
            self._log("UART closed")
