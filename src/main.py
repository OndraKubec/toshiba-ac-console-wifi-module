# main.py — start web, Modbus TCP, and CN22 UART bridge

import uasyncio as asyncio
from web_server import WebServer
from wifi import WiFiManager
from modbus_tcp import ModbusTCPServer, ACModel, update_rssi
from toshiba_cn22_driver import ToshibaCN22Driver


async def main():
    wifi = WiFiManager()
    print("[main] Connecting to Wi-Fi...")
    ip = wifi.connect()
    print(f"[main] Wi-Fi connected, IP: {ip}")

    # Launch background keepalive
    asyncio.create_task(wifi.keepalive())

    web = WebServer(host="0.0.0.0", port=80)
    await web.start()

    # Model + driver
    print("[main] Initializing ACModel and ToshibaCN22Driver...")
    model = ACModel(driver=None)
    driver = ToshibaCN22Driver(model)  # TX=32, RX=33 inside driver
    model.driver = driver  # inject
    print("[main] Model and driver initialized.")

    # Start Modbus TCP
    print("[main] Starting Modbus TCP server...")
    server = ModbusTCPServer(model, host=ip, port=502, unit_id=1)
    await server.start()
    print("[main] Modbus TCP server started.")

    # Bring up UART side: handshake + tasks
    print("[main] Starting CN22 handshake...")
    await driver.handshake()
    print("[main] Handshake done. Starting keepalive, poll and event watch tasks...")
    asyncio.create_task(driver.keepalive_task())
    asyncio.create_task(driver.poll_task(every_ms=30000))
    asyncio.create_task(driver.event_watch_task())
    asyncio.create_task(update_rssi(model, wifi))
    print("[main] Tasks started. Parking forever.")

    # park forever
    await asyncio.Event().wait()


asyncio.run(main())
