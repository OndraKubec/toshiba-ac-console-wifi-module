# modbus_tconnect_fake.py
# Fake "TConnect over UART" via Modbus TCP - MicroPython (uasyncio)
import uasyncio as asyncio
import struct
import time

# Modbus exception codes
_ILLEGAL_FUNCTION = 0x01
_ILLEGAL_DATA_ADDRESS = 0x02
_ILLEGAL_DATA_VALUE = 0x03
_SERVER_DEVICE_FAILURE = 0x04

# Value helpers
INT16_MIN = -32768


def _u16(x):
    return x & 0xFFFF


def _s16(x):
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


# Simple model matching TConnect's status fields
class ACModel:
    # enums to keep it tidy
    MODE_AUTO, MODE_COOL, MODE_HEAT, MODE_DRY, MODE_FAN = 0, 1, 2, 3, 4
    FAN_QUIET, FAN_1, FAN_2, FAN_3, FAN_4, FAN_5, FAN_AUTO = 0, 1, 2, 3, 4, 5, 6

    def __init__(self):
        # mirror to "holding" regs
        self.state = 0  # 0=OFF, 1=ON
        self.mode = self.MODE_FAN
        self.fanmode = self.FAN_AUTO
        self.swing = 0  # 0=OFF, 1=ON
        self.setpoint_x10 = 220  # 22.0 C
        self.roomtemp_x10 = 240  # 24.0 C
        self.outdoor_x10 = INT16_MIN  # "-" unknown
        self.rssi_dbm = -58

    # packing/unpacking holding regs 0..7
    def to_regs(self):
        vals = [
            self.state,
            self.mode,
            self.fanmode,
            self.swing,
            _u16(self.setpoint_x10),
            _u16(self.roomtemp_x10),
            _u16(self.outdoor_x10 if self.outdoor_x10 != None else INT16_MIN),
            _u16(self.rssi_dbm),
        ]
        return vals

    def apply_write(self, addr, vals):
        # Only allow writes to: state, mode, fanmode, swing, setpoint
        for i, raw in enumerate(vals):
            a = addr + i
            v = _s16(raw)
            if a == 0:  # state
                if v not in (0, 1):
                    raise ValueError
                self.state = v
            elif a == 1:  # mode
                if v not in (0, 1, 2, 3, 4):
                    raise ValueError
                self.mode = v
            elif a == 2:  # fanmode
                if v not in (0, 1, 2, 3, 4, 5, 6):
                    raise ValueError
                self.fanmode = v
            elif a == 3:  # swing
                if v not in (0, 1):
                    raise ValueError
                self.swing = v
            elif a == 4:  # setpoint
                if v < 160 or v > 300:  # 16.0°C..30.0°C
                    raise ValueError
                self.setpoint_x10 = v
            else:
                # ignore writes to read-only slots (room temp / outdoor / rssi)
                pass


class ModbusTCPServer:
    """Minimal Modbus TCP server: FC 03, 04, 06, 16."""

    def __init__(self, model: ACModel, host="0.0.0.0", port=502, unit_id=1):
        self.model = model
        self.host = host
        self.port = port
        self.uid = unit_id
        self._server = None

    async def start(self):
        self._server = await asyncio.start_server(self._client, self.host, self.port)
        print("Modbus TCP on %s:%d (UID=%d)" % (self.host, self.port, self.uid))
        return self._server

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _client(self, reader, writer):
        try:
            while True:
                # MBAP (7 bytes)
                hdr = await reader.readexactly(7)
                tid, pid, length, uid = struct.unpack(">HHHB", hdr)
                if pid != 0:
                    await self._discard(reader, length - 1)
                    continue
                pdu_len = length - 1
                pdu = await reader.readexactly(pdu_len)
                fc = pdu[0]
                data = pdu[1:]
                try:
                    resp_pdu = self._process(fc, data)
                except ValueError:
                    resp_pdu = bytes([fc | 0x80, _ILLEGAL_DATA_VALUE])
                except IndexError:
                    resp_pdu = bytes([fc | 0x80, _ILLEGAL_DATA_ADDRESS])
                except Exception:
                    resp_pdu = bytes([fc | 0x80, _SERVER_DEVICE_FAILURE])
                mbap = struct.pack(">HHHB", tid, 0, len(resp_pdu) + 1, uid)
                try:
                    await writer.awrite(mbap + resp_pdu)
                except OSError:
                    break
        except (asyncio.IncompleteReadError, OSError):
            pass
        finally:
            try:
                await writer.aclose()
            except:
                pass

    def _process(self, fc, data):
        regs = self.model.to_regs()
        # FC 03 / FC 04: read registers
        if fc in (0x03, 0x04):
            if len(data) != 4:
                raise ValueError
            addr, qty = struct.unpack(">HH", data)
            if qty < 1 or qty > 125:
                raise ValueError
            end = addr + qty
            if end > len(regs):
                raise IndexError
            payload = b"".join(struct.pack(">H", _u16(r)) for r in regs[addr:end])
            return bytes([fc, len(payload)]) + payload

        # FC 06: write single holding register
        if fc == 0x06:
            if len(data) != 4:
                raise ValueError
            addr, value = struct.unpack(">HH", data)
            self.model.apply_write(addr, [value])
            return bytes([0x06]) + struct.pack(">HH", addr, value)

        # FC 16: write multiple holding registers
        if fc == 0x10:
            if len(data) < 5:
                raise ValueError
            addr, qty, byte_count = struct.unpack(">HHB", data[:5])
            if qty < 1 or qty > 123 or byte_count != qty * 2:
                raise ValueError
            if len(data[5:]) != byte_count:
                raise ValueError
            values = [
                struct.unpack(">H", data[5 + i : 7 + i])[0]
                for i in range(0, byte_count, 2)
            ]
            self.model.apply_write(addr, values)
            return bytes([0x10]) + struct.pack(">HH", addr, qty)

        # Unsupported function
        return bytes([fc | 0x80, _ILLEGAL_FUNCTION])

    async def _discard(self, reader, n):
        if n > 0:
            try:
                await reader.readexactly(n)
            except:
                pass


# Optional: a tiny task to “fake” changing temps/RSSI over time
async def simulate_sensors(model: ACModel):
    while True:
        # drift room temp slightly toward setpoint if state=ON
        rt = model.roomtemp_x10
        sp = model.setpoint_x10
        if model.state == 1:
            if rt < sp:
                rt += 1
            elif rt > sp:
                rt -= 1
        model.roomtemp_x10 = rt
        # random-ish outdoor temp placeholder
        t = int(time.ticks_ms() // 1000) % 60
        model.outdoor_x10 = 50 + t  # 5.0°C .. 11.0°C
        await asyncio.sleep(1)
