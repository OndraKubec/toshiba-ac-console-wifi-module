import uasyncio as asyncio
import time
import config
from machine import UART, Pin
from logger import Logger

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
NO_RESPONSE_RECOVER = 2
NO_RESPONSE_RECOVER_DELAY_MS = 2000

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

# ========================= COMMAND IDS / MAPS ====================
CMD_POWER = 0x80
CMD_MODE = 0xB0
CMD_FAN = 0x90
CMD_SWING = 0xA3  # swing uses SIMPLE 0xA3 with 0x41(on)/0x31(off)
CMD_P_SELECT = 0x87  # p-select uses SIMPLE 0x87 with percentage value
VAL_SWING_ON, VAL_SWING_OFF = 0x41, 0x31
VAL_POWER_ON, VAL_POWER_OFF = 0x30, 0x31
VAL_OTHER_OFF = 0x00  # F8 "other" byte (extra features from OEM app)
VAL_OTHER_HI_POWER = 0x01
VAL_OTHER_OUTDOOR_SILENT_ON = 0x02
VAL_OTHER_ECO = 0x03
VAL_OTHER_8DEG = 0x04
VAL_OTHER_FLOOR = 0x06
VAL_OTHER_OUTDOOR_SILENT_MID = 0x0A
VAL_OTHER_HEAT_EXCHANGE_ON = 0x20
VAL_OTHER_HEAT_EXCHANGE_MID = 0x30

# Mode codes — ACModel order: 0=auto,1=cool,2=heat,3=dry,4=fan
MODE_CODE = {0: 0x41, 1: 0x42, 2: 0x43, 3: 0x44, 4: 0x45}
MODE_CODE_REV = {v: k for k, v in MODE_CODE.items()}

# Fan codes — quiet..5; auto is handled as “mode+temp only”
FAN_CODE = {0: 0x31, 1: 0x32, 2: 0x33, 3: 0x34, 4: 0x35, 5: 0x36}
FAN_CODE_REV = {v: k for k, v in FAN_CODE.items()}

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
    body = (
        BASE_EXT + b"\xf8" + b"".join(bytes([r & 0xFF, v & 0xFF]) for (r, v) in pairs)
    )
    return body + bytes([_chk_to_0x02(body)])


def _clamp_temp(t):
    return 20 if t < 20 else 30 if t > 30 else t


def _fan_code_or_auto(fan):
    if fan == 6:
        return 0x41
    return FAN_CODE.get(fan, 0x32)


def _build_set_all_ext(temp_c, mode, fan, other=0x00):
    code = MODE_CODE.get(mode, 0x41)
    t = _clamp_temp(temp_c)
    f = _fan_code_or_auto(fan)
    return _build_ext_pairs([(code, t), (f, other & 0xFF)])


def _parse_0x90_frames(stream):
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


def _parse_single_event_frame(fr):
    if len(fr) < 10 or fr[:4] != b"\x02\x00\x03\x11":
        return None
    ln = fr[6]
    total = 7 + ln + 1
    if total != len(fr):
        return None
    sid, val, chk = fr[-3], fr[-2], fr[-1]
    ok = ((sum(fr[:-1]) + chk) & 0xFF) == 0x02
    return sid, val, ok


def _parse_0x11_frames(stream):
    out = []
    if not stream:
        return out
    i, n = 0, len(stream)
    while i + 8 <= n:
        if stream[i : i + 4] != b"\x02\x00\x03\x11":
            i += 1
            continue
        ln = stream[i + 6]
        total = 7 + ln + 1
        if i + total > n:
            break
        fr = stream[i : i + total]
        parsed = _parse_single_event_frame(fr)
        if parsed:
            out.append(parsed)
        i += total
    return out


def _build_ack_91():
    body = bytes(
        [0x02, 0x00, 0x03, 0x91, 0x01, 0x00, 0x05, 0x00, 0x00, 0x01, 0x30, 0x01]
    )
    return body + bytes([_chk_to_0x02(body)])


# ========================= DRIVER ================================
class ToshibaCN22Driver:
    def __init__(self, model, tx_pin=TX_PIN, rx_pin=RX_PIN, uart_id=UART_ID, debug=2):
        self.model = model
        self.tx_pin = tx_pin
        self.rx_pin = rx_pin
        self.uart_id = uart_id
        self.uart = None
        self._handshaked = False
        self._missed_keepalive = 0
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._recovering = False
        self._no_response_count = 0
        self._busy = False
        self.debug = int(debug)  # 0: off, 1: basic, 2: hex
        self.log = Logger("cn22-driver")

    # ---- debug helpers via Logger ----
    def set_debug(self, level):
        self.debug = int(level)

    def _hex(self, b):
        try:
            return " ".join("%02X" % x for x in b)
        except:
            return str(b)

    # ---- low-level I/O ----
    def _mk_uart(self):
        if self.debug >= 1:
            self.log.debug(
                "UART init: id=%d tx=%d rx=%d baud=%d bits=%d parity=%s stop=%d rxbuf=%d"
                % (
                    self.uart_id,
                    self.tx_pin,
                    self.rx_pin,
                    BAUD,
                    BITS,
                    "EVEN",
                    STOP,
                    RXBUF,
                )
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
                if self.debug >= 1:
                    self.log.debug(f"UART read error: {e}")
                d = None
            if d:
                chunks += 1
                buf.extend(d)
        if self.debug >= 1:
            self.log.debug(f"RX collect: {ms} ms, {chunks} chunks, {len(buf)} bytes")
            if self.debug >= 2 and buf:
                self.log.debug("RX hex: " + self._hex(buf))
        return bytes(buf)

    async def _wait_idle(self, idle_ms=30, timeout_ms=1200):
        t0 = time.ticks_ms()
        last = t0
        while True:
            try:
                d = self.uart.read()
            except Exception as e:
                if self.debug >= 1:
                    self.log.debug(f"UART read error during wait_idle: {e}")
                d = None
            if d:
                last = time.ticks_ms()
            if time.ticks_diff(time.ticks_ms(), last) >= idle_ms:
                if self.debug >= 1:
                    self.log.debug(f"wait_idle: idle for {idle_ms} ms")
                return True
            if time.ticks_diff(time.ticks_ms(), t0) >= timeout_ms:
                if self.debug >= 1:
                    self.log.debug(f"wait_idle: TIMEOUT after {timeout_ms} ms")
                return False
            await asyncio.sleep_ms(4)

    async def _send(
        self,
        label,
        frame,
        wait_ms=WAIT_RX_MS,
        gap_ms=GAP_SHORT_MS,
        verbose=False,
        track_no_response=True,
    ):
        rsp = b""
        async with self._send_lock:
            self._busy = True
            try:
                # drain any pending RX
                try:
                    while True:
                        d = self.uart.read()
                        if not d:
                            break
                except Exception as e:
                    if self.debug >= 1:
                        self.log.debug(f"UART drain error: {e}")

                idle_ok = await self._wait_idle(30, 1200)
                if self.debug >= 1:
                    self.log.debug(f"TX {label} len={len(frame)} idle_ok={idle_ok}")
                    if self.debug >= 2:
                        self.log.debug("TX hex: " + self._hex(frame))

                self.uart.write(frame)
                rsp = await self._rx_collect(wait_ms)

                if self.debug >= 1:
                    if rsp:
                        self.log.debug(f"RX {label} len={len(rsp)}")
                    else:
                        self.log.debug(f"RX {label}: <no response>")

                await asyncio.sleep_ms(gap_ms)
                self._reflect_stream(rsp)
            finally:
                self._busy = False
        if track_no_response and wait_ms > 0:
            if rsp:
                self._no_response_count = 0
            else:
                self._no_response_count += 1
                if self._no_response_count >= NO_RESPONSE_RECOVER:
                    self._no_response_count = 0
                    await self._recover_session()
        return rsp

    async def _write_frame(self, label, frame):
        async with self._send_lock:
            self._busy = True
            try:
                if self.debug >= 1:
                    self.log.debug(f"TX {label} len={len(frame)} (write-only)")
                    if self.debug >= 2:
                        self.log.debug("TX hex: " + self._hex(frame))
                self.uart.write(frame)
            finally:
                self._busy = False

    async def _recover_session(self):
        if self._recovering:
            return
        self._recovering = True
        try:
            self.log.warning(
                f"No response {NO_RESPONSE_RECOVER}x -> re-handshake in {NO_RESPONSE_RECOVER_DELAY_MS} ms"
            )
            self._handshaked = False
            await asyncio.sleep_ms(NO_RESPONSE_RECOVER_DELAY_MS)
            if self.debug >= 1:
                self.log.debug("Re-handshake starting now")
            await self.handshake()
        finally:
            self._recovering = False

    # ---- session maintenance ----
    async def _handshake_once(self, per_step_retries=2):
        """Run one handshake sequence. Returns (any_reply: bool, missed_labels: list[str])."""
        steps = [
            ("HS0", HS[0], GAP_NORMAL_MS, WAIT_RX_MS, False),
            ("HS1", HS[1], GAP_SHORT_MS, WAIT_RX_MS, False),
            ("HS2", HS[2], GAP_NORMAL_MS, WAIT_RX_MS, False),
            ("HS3", HS[3], GAP_SHORT_MS, 0, True),
            ("HS4", HS[4], GAP_SHORT_MS, WAIT_RX_MS, False),
            ("HS5", HS[5], GAP_NORMAL_MS, WAIT_RX_MS, False),
            ("AF0", AFTER_HS[0], GAP_SHORT_MS, WAIT_RX_MS, False),
            ("AF1", AFTER_HS[1], GAP_NORMAL_MS, WAIT_RX_MS, False),
        ]
        any_reply = False
        missed = []
        for label, frame, gap, wait_ms, allow_no_response in steps:
            rsp = None
            for _ in range(per_step_retries):
                rsp = await self._send(
                    label,
                    frame,
                    wait_ms=wait_ms,
                    gap_ms=gap,
                    verbose=(self.debug >= 1),
                    track_no_response=False,
                )
                if rsp:
                    any_reply = True
                    break
                if allow_no_response:
                    rsp = b""
                    break
                await asyncio.sleep_ms(80)  # small spacing between retries
            if not rsp and not allow_no_response:
                missed.append(label)
        return any_reply, missed

    async def handshake(
        self, base_backoff_ms=500, max_backoff_ms=8000, per_step_retries=2
    ):
        """Handshake with repeats + exponential backoff. Never 'fail hard'."""
        async with self._lock:
            if self.uart is None:
                self.uart = self._mk_uart()
            self._handshaked = False

            if self.debug >= 1:
                self.log.debug(f"Handshake: start (delay {START_DELAY_MS} ms)")
            await asyncio.sleep_ms(START_DELAY_MS)

            backoff = base_backoff_ms
            attempt = 0
            while True:
                attempt += 1
                if self.debug >= 1:
                    self.log.debug(f"Handshake attempt {attempt}")
                any_reply, missed = await self._handshake_once(
                    per_step_retries=per_step_retries
                )

                if any_reply:
                    self._handshaked = True
                    self._missed_keepalive = 0
                    if missed:
                        self.log.warning(
                            "Handshake: no response for steps: " + ",".join(missed)
                        )
                    self.log.info("Handshake: DONE")
                    return

                # No replies at all → backoff and try again
                self.log.warning(
                    f"Handshake: no device response (attempt {attempt}) "
                    f"→ retrying in {backoff} ms"
                )
                await asyncio.sleep_ms(backoff)
                backoff = min(backoff * 2, max_backoff_ms)

    async def keepalive_task(self):
        while True:
            try:
                if not self._handshaked:
                    if self.debug >= 1:
                        self.log.debug("keepalive: not handshaked -> handshake()")
                    await self.handshake()
                    await asyncio.sleep_ms(1000)
                    continue
                ok = False
                for i, fr in enumerate(AFTER_HS):
                    rsp = await self._send(
                        f"KA{i}",
                        fr,
                        wait_ms=300,
                        gap_ms=GAP_SHORT_MS,
                        verbose=(self.debug >= 1),
                        track_no_response=False,
                    )
                    if rsp:
                        ok = True
                if not ok:
                    self._missed_keepalive += 1
                    if self.debug >= 1:
                        self.log.debug(f"keepalive: missed ({self._missed_keepalive})")
                    if self._missed_keepalive >= REHANDSHAKE_MISSED:
                        if self.debug >= 1:
                            self.log.debug("keepalive: re-handshake")
                        self._handshaked = False
                        self._missed_keepalive = 0
                else:
                    if self._missed_keepalive and self.debug >= 1:
                        self.log.debug("keepalive: recovered")
                    self._missed_keepalive = 0
                await asyncio.sleep_ms(KEEPALIVE_EVERY_MS)
            except Exception as e:
                self.log.error(f"keepalive_task error: {e}")
                await asyncio.sleep_ms(500)

    # ---- reflect replies into model ----
    def _reflect_sid_val(self, sid, val):
        if self.debug >= 2:
            self.log.debug("reflect SID=0x%02X VAL=0x%02X" % (sid, val))
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
        elif sid == 0xA0:
            if val == 0x41:
                self.model.fanmode = 6
            elif val in FAN_CODE_REV:
                self.model.fanmode = FAN_CODE_REV[val]
        elif sid == 0xB3:
            self.model.setpoint_x10 = int(val) * 10
        elif sid == 0xA3:
            self.model.swing = 1 if val == VAL_SWING_ON else 0
        elif sid == 0x87:
            self.model.p_select = int(val)
        elif sid == 0xF7:
            self.model.other = int(val)
        elif sid == 0x41:
            self.model.setpoint_x10 = int(val) * 10
        elif sid == 0xBB:
            self.model.roomtemp_x10 = int(val) * 10
        elif sid == 0xBE:
            v = int(val)
            if v > 128:
                v -= 256
            self.model.outdoor_x10 = v * 10

    def _reflect_stream(self, rsp):
        if not rsp:
            return
        frames = _parse_0x90_frames(rsp)
        if frames and self.debug >= 1:
            self.log.debug(f"reflect_stream: {len(frames)} x 0x90 frames")
        for sid, val, ok in frames:
            if ok:
                self._reflect_sid_val(sid, val)
            elif self.debug >= 1:
                self.log.debug(
                    "reflect: BAD CHK for SID=0x%02X VAL=0x%02X" % (sid, val)
                )
        evt_frames = _parse_0x11_frames(rsp)
        if evt_frames and self.debug >= 1:
            self.log.debug(f"reflect_stream: {len(evt_frames)} x 0x11 frames")
        for sid, val, ok in evt_frames:
            if ok:
                self._reflect_sid_val(sid, val)
            elif self.debug >= 1:
                self.log.debug(
                    "reflect_evt: BAD CHK for SID=0x%02X VAL=0x%02X" % (sid, val)
                )

    def _reflect_event_frame(self, fr):
        parsed = _parse_single_event_frame(fr)
        if not parsed:
            return
        sid, val, ok = parsed
        if ok:
            if self.debug >= 1:
                self.log.debug(
                    "reflect_event SID=0x%02X VAL=0x%02X" % (sid, val)
                )
            self._reflect_sid_val(sid, val)
        elif self.debug >= 1:
            self.log.debug(
                "reflect_event: BAD CHK for SID=0x%02X VAL=0x%02X" % (sid, val)
            )

    # ---- quick helper: get current mode_code (read if unknown) ----
    async def _get_current_mode_code(self):
        cur_mode = getattr(self.model, "mode", None)
        if cur_mode is not None and 0 <= int(cur_mode) <= 4:
            return MODE_CODE.get(int(cur_mode), 0x41)
        try:
            rsp = await self._send(
                "READB0(cur_mode)",
                _build_read(0xB0),
                wait_ms=POLL_WAIT_MS,
                gap_ms=GAP_SHORT_MS,
                track_no_response=False,
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
            if self.debug >= 1:
                self.log.debug(f"get_current_mode_code read failed: {e}")
        return 0x41  # fallback AUTO

    # ---- polling task ----
    async def poll_task(self, every_ms=2000):
        _POLL_SIDS = (0x80, 0xB0, 0x90, 0xA3, 0x87, 0xBB, 0xBE)  # power/mode/fan/swing/p-select/temps
        while True:
            try:
                if not self._handshaked:
                    if self.debug >= 1:
                        self.log.debug("poll: not handshaked -> handshake()")
                    await self.handshake()
                    await asyncio.sleep_ms(250)
                    continue
                for sid in _POLL_SIDS:
                    if self.debug >= 1:
                        self.log.debug("poll: READ 0x%02X" % sid)
                    _ = await self._send(
                        f"READ{sid:02X}",
                        _build_read(sid),
                        wait_ms=POLL_WAIT_MS,
                        verbose=(self.debug >= 1),
                        track_no_response=False,
                    )
                await asyncio.sleep_ms(every_ms)
            except Exception as e:
                self.log.error(f"poll_task error: {e}")
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
                    if self.debug >= 1:
                        self.log.debug(f"UART read error in event_watch: {e}")
                    d = None
                if d:
                    buf.extend(d)
                frames = self._feed_frames(buf)
                if frames and self.debug >= 1:
                    self.log.debug(f"event_watch: {len(frames)} frames")
                for fr in frames:
                    if len(fr) >= 7 and fr[:3] == b"\x02\x00\x03":
                        typ = fr[3]
                        if self.debug >= 2:
                            self.log.debug(
                                "EVT frame typ=0x%02X len=%d hex=%s"
                                % (typ, len(fr), self._hex(fr))
                            )
                        if typ == 0x90:
                            self._reflect_stream(fr)
                        elif typ == 0x11:
                            try:
                                if self.debug >= 1:
                                    self.log.debug(
                                        "EVT: 0x11 -> reflect + send 0x91 ACK"
                                    )
                                self._reflect_event_frame(fr)
                                await self._write_frame("ACK_91", _build_ack_91())
                            except Exception as e:
                                self.log.error(f"ACK 0x91 write error: {e}")
                await asyncio.sleep_ms(2)
            except Exception as e:
                self.log.error(f"event_watch_task error: {e}")
                await asyncio.sleep_ms(200)

    # ==================== PUBLIC API ====================
    async def set_settings(self, mode, fan, setpoint_x10, other=0x00):
        m0 = int(mode) if mode is not None else 0
        f0 = int(fan) if fan is not None else 6
        if other is None:
            other = getattr(self.model, "other", VAL_OTHER_OFF)
        other = int(other) & 0xFF
        m = 0 if m0 < 0 else 4 if m0 > 4 else m0
        f = 0 if f0 < 0 else 6 if f0 > 6 else f0
        t = _clamp_temp(setpoint_x10 // 10)
        if not self._handshaked:
            await self.handshake()
        await self._send(
            f"SET_ALL(m={m} f={f} t={t})",
            _build_set_all_ext(t, m, f, other),
            wait_ms=800,
            gap_ms=GAP_NORMAL_MS,
            verbose=(self.debug >= 1),
        )
        self.model.mode = m
        self.model.fanmode = f
        self.model.setpoint_x10 = t * 10
        if other is not None:
            self.model.other = other

    async def set_other(self, other):
        if not self._handshaked:
            await self.handshake()
        cur_mode = getattr(self.model, "mode", None)
        cur_fan = getattr(self.model, "fanmode", None)
        cur_sp = getattr(self.model, "setpoint_x10", 220)
        await self.set_settings(cur_mode, cur_fan, cur_sp, other=other)

    async def set_p_select(self, val):
        if not self._handshaked:
            await self.handshake()
        v = int(val) & 0xFF
        await self._send(
            f"P_SELECT({v})",
            _build_simple(CMD_P_SELECT, v),
            wait_ms=800,
            gap_ms=GAP_NORMAL_MS,
            verbose=(self.debug >= 1),
        )
        self.model.p_select = v

    async def set_setpoint_x10(self, sp_x10):
        t = _clamp_temp(sp_x10 // 10)
        if not self._handshaked:
            await self.handshake()
        cur_mode = getattr(self.model, "mode", None)
        mode = int(cur_mode) if cur_mode is not None else None
        if mode is None or not (0 <= mode <= 4):
            mode_code = await self._get_current_mode_code()
            mode = MODE_CODE_REV.get(mode_code, 0)
        cur_fan = getattr(self.model, "fanmode", None)
        fan = int(cur_fan) if cur_fan is not None else 6
        if not (0 <= fan <= 6):
            fan = 6
        other = getattr(self.model, "other", VAL_OTHER_OFF)
        await self._send(
            f"SET_TEMP({t}) keep-mode",
            _build_set_all_ext(t, mode, fan, other),
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
            temp_c = _clamp_temp(self.model.setpoint_x10 // 10)
            cur_mode = getattr(self.model, "mode", None)
            if cur_mode is not None and 0 <= int(cur_mode) <= 4:
                mode = int(cur_mode)
            else:
                mode_code = await self._get_current_mode_code()
                mode = MODE_CODE_REV.get(mode_code, 0)
            cur_fan = getattr(self.model, "fanmode", None)
            fan = int(cur_fan) if cur_fan is not None else 6
            if not (0 <= fan <= 6):
                fan = 6
            other = getattr(self.model, "other", VAL_OTHER_OFF)
            await self._send(
                "SET_TEMP keep-mode after OFF",
                _build_set_all_ext(temp_c, mode, fan, other),
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
        cur_fan = getattr(self.model, "fanmode", None)
        fan = int(cur_fan) if cur_fan is not None else 6
        if not (0 <= fan <= 6):
            fan = 6
        other = getattr(self.model, "other", VAL_OTHER_OFF)
        await self._send(
            f"MODE_EXT({m})",
            _build_set_all_ext(temp_c, m, fan, other),
            wait_ms=800,
            gap_ms=GAP_NORMAL_MS,
            verbose=(self.debug >= 1),
        )
        self.model.mode = m

    async def set_fanmode(self, fan):
        f = 0 if fan < 0 else 6 if fan > 6 else fan
        if not self._handshaked:
            await self.handshake()
        temp_c = _clamp_temp(self.model.setpoint_x10 // 10)
        cur_mode = getattr(self.model, "mode", None)
        if cur_mode is not None and 0 <= int(cur_mode) <= 4:
            mode = int(cur_mode)
        else:
            mode_code = await self._get_current_mode_code()
            mode = MODE_CODE_REV.get(mode_code, 0)
        other = getattr(self.model, "other", VAL_OTHER_OFF)
        await self._send(
            f"FAN_EXT({f})",
            _build_set_all_ext(temp_c, mode, f, other),
            wait_ms=800,
            gap_ms=GAP_NORMAL_MS,
            verbose=(self.debug >= 1),
        )
        self.model.fanmode = f

    async def set_swing(self, on):
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
        self.model.swing = v

    async def close(self):
        if self.uart:
            try:
                self.uart.deinit()
            except Exception as e:
                self.log.error(f"UART deinit error: {e}")
            self.uart = None
            self._handshaked = False
            if self.debug >= 1:
                self.log.debug("UART closed")
