import uasyncio as asyncio
import struct
from logger import Logger


_ILLEGAL_FUNCTION = 0x01
_ILLEGAL_DATA_ADDRESS = 0x02
_ILLEGAL_DATA_VALUE = 0x03
_SERVER_DEVICE_FAILURE = 0x04


def _u16(x):
    return x & 0xFFFF


class ModbusTCPServer:
    def __init__(self, model, host="0.0.0.0", port=502, unit_id=1):
        self.model = model
        self.host = host
        self.port = port
        self.uid = unit_id
        self._server = None
        self.log = Logger("modbus")

    async def start(self):
        self._server = await asyncio.start_server(self._client, self.host, self.port)
        self.log.info(f"Modbus TCP on {self.host}:{self.port} (UID={self.uid})")
        return self._server

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self.log.info("Server stopped")

    async def _client(self, reader, writer):
        try:
            while True:
                hdr = await reader.readexactly(7)  # EOFError on disconnect
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
        except (EOFError, OSError):
            self.log.debug("Client closed / socket error")
        finally:
            try:
                await writer.aclose()
            except:
                pass

    def _process(self, fc, data):
        regs = self.model.to_regs()

        # FC 03 / 04
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

        # FC 06
        if fc == 0x06:
            if len(data) != 4:
                raise ValueError
            addr, value = struct.unpack(">HH", data)
            self.model.apply_write(addr, [value])
            return bytes([0x06]) + struct.pack(">HH", addr, value)

        # FC 16
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

        # Unsupported
        return bytes([fc | 0x80, _ILLEGAL_FUNCTION])

    async def _discard(self, reader, n):
        if n > 0:
            try:
                await reader.readexactly(n)
            except:
                pass
