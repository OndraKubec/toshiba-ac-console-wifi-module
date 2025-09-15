import uasyncio as asyncio
from web_server import WebServer
from wifi import WiFiManager
from ac_model import ACModel, rssi_task
from modbus_tcp import ModbusTCPServer
from toshiba_cn22_driver import ToshibaCN22Driver
from logger import Logger


async def main():
    log = Logger("main")

    # Wi-Fi (WiFiManager logs its own steps)
    wifi = WiFiManager()
    ip = wifi.connect()
    asyncio.create_task(wifi.keepalive())

    # HTTP server (logs inside WebServer)
    web = WebServer(host=ip, port=80)
    await web.start()

    # Model + driver (driver logs handshake/traffic when debug enabled)
    model = ACModel(driver=None)
    driver = ToshibaCN22Driver(model, debug=1)
    model.driver = driver

    # Modbus TCP (bind all interfaces to survive DHCP IP changes)
    mbs = ModbusTCPServer(model, host=ip, port=502, unit_id=1)
    await mbs.start()

    # RSSI updater (simple background task)
    asyncio.create_task(rssi_task(model, wifi))

    # CN22 handshake + background tasks
    await driver.handshake()
    asyncio.create_task(driver.keepalive_task())
    asyncio.create_task(driver.poll_task(every_ms=15000))
    asyncio.create_task(driver.event_watch_task())

    log.info("System up. Parking forever.")
    await asyncio.Event().wait()


asyncio.run(main())
