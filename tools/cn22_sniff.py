# cn22_sniff.py — Dual-UART sniffer & logger for Toshiba CN22 (ESP32 MicroPython)
# Listens on two RX lines simultaneously and logs frames split by idle gaps.
# UARTs:
#   A: UART(1)  TX=25  RX=26
#   B: UART(2)  TX=32  RX=33
# Serial: 9600 baud, 8E1 (EVEN parity) — adjust if needed.

import uasyncio as asyncio
from machine import UART, Pin
import sys
import os
import time

# ---------- CONFIG ----------
BAUD = 9600
BITS = 8
PARITY = 0  # EVEN on ESP32 MicroPython: 0=EVEN, 1=ODD, None=None
STOP = 1
RXBUF = 4096

# UART pin mapping (you gave these)
UART_A_ID, UART_A_TX, UART_A_RX = 1, 25, 26
UART_B_ID, UART_B_TX, UART_B_RX = 2, 32, 33

# Frame split when no bytes arrive for this long (milliseconds)
FRAME_GAP_MS = 20
# Poll interval for reads (milliseconds)
SLICE_MS = 2


# -------------- utils --------------
def hx(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def ticks_ms() -> int:
    return time.ticks_ms()


def ticks_diff(a, b) -> int:
    return time.ticks_diff(a, b)


# -------------- logger --------------
class Logger:
    def __init__(self):
        self.start = ticks_ms()
        self.filename = f"cn22_sniff_{self.start}.log"
        try:
            self.f = open(self.filename, "a")
            self.f.write(
                f"# cn22_sniff start_ms={self.start} baud={BAUD} parity={'EVEN' if PARITY==0 else PARITY}\n"
            )
            self.f.flush()
        except Exception as e:
            print("! Could not open log file:", e)
            self.f = None

    def log(self, src: str, t_ms: int, b: bytes):
        rel = ticks_diff(t_ms, self.start)
        line = f"{rel:09d}ms {src} len={len(b):3d} : {hx(b)}\n"
        # Print to terminal
        print(line, end="")
        # Append to file
        if self.f:
            try:
                self.f.write(line)
                self.f.flush()
            except Exception as e:
                print("! Write error:", e)

    def close(self):
        if self.f:
            try:
                self.f.close()
            except:
                pass


# -------------- sniffer task --------------
class Sniffer:
    def __init__(self, name: str, uart: UART, logger: Logger):
        self.name = name
        self.uart = uart
        self.log = logger
        self.buf = bytearray()
        self.last_rx = None
        self.frame_start = None

    async def run(self):
        while True:
            d = self.uart.read()
            now = ticks_ms()
            if d:
                # first byte of a new frame
                if not self.buf:
                    self.frame_start = now
                self.buf += d
                self.last_rx = now
            else:
                # no data — check for frame gap
                if self.buf and self.last_rx is not None:
                    if ticks_diff(now, self.last_rx) >= FRAME_GAP_MS:
                        # flush frame
                        self.log.log(self.name, self.frame_start, bytes(self.buf))
                        self.buf = bytearray()
                        self.last_rx = None
                        self.frame_start = None
            await asyncio.sleep_ms(SLICE_MS)


# -------------- setup --------------
def make_uart(uid, tx_pin, rx_pin):
    return UART(
        uid,
        baudrate=BAUD,
        bits=BITS,
        parity=PARITY,
        stop=STOP,
        tx=Pin(tx_pin),
        rx=Pin(rx_pin),
        rxbuf=RXBUF,
    )


async def main():
    print(
        f"Starting dual-UART sniffer  (baud={BAUD}, {BITS}E{STOP if PARITY==0 else STOP})"
    )
    print(f"A: UART{UART_A_ID} TX={UART_A_TX} RX={UART_A_RX}")
    print(f"B: UART{UART_B_ID} TX={UART_B_TX} RX={UART_B_RX}")
    logger = Logger()
    ua = make_uart(UART_A_ID, UART_A_TX, UART_A_RX)
    ub = make_uart(UART_B_ID, UART_B_TX, UART_B_RX)

    snif_a = Sniffer("A", ua, logger)
    snif_b = Sniffer("B", ub, logger)

    try:
        await asyncio.gather(snif_a.run(), snif_b.run())
    finally:
        logger.close()
        try:
            ua.deinit()
        except:
            pass
        try:
            ub.deinit()
        except:
            pass


asyncio.run(main())
