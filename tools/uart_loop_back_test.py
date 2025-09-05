# uart_loop_test.py
from machine import UART, Pin
import time, ubinascii


def main(
    uart_id=1,
    tx_pin=25,
    rx_pin=26,
    baud=9600,
    pattern=b"hello",
    delay_ms=60,
    pause_ms=200,
):
    u = UART(
        uart_id,
        baudrate=baud,
        bits=8,
        parity=None,
        stop=1,
        tx=Pin(tx_pin),
        rx=Pin(rx_pin),
    )
    print("UART", uart_id, "TX=", tx_pin, "RX=", rx_pin, "baud=", baud)
    print("Sending pattern:", pattern)

    while True:
        u.write(pattern)
        # 5 bytes @2400 bps ≈ 21 ms; give margin for the shifter
        time.sleep_ms(delay_ms)

        n = u.any()
        data = u.read(n) if n else None
        print(
            "n=",
            n,
            " raw=",
            data,
            " hex=",
            (ubinascii.hexlify(data).decode() if data else None),
        )
        time.sleep_ms(pause_ms)


if __name__ == "__main__":
    main()
