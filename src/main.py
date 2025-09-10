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
    _ = wifi.connect()
    asyncio.create_task(wifi.keepalive())

    # HTTP server (logs inside WebServer)
    web = WebServer(host="0.0.0.0", port=80)
    await web.start()

    # Model + driver (driver logs handshake/traffic when debug enabled)
    model = ACModel(driver=None)
    driver = ToshibaCN22Driver(model)  # set driver.debug=1/2 for more logs
    model.driver = driver

    # Modbus TCP (bind all interfaces to survive DHCP IP changes)
    mbs = ModbusTCPServer(model, host="0.0.0.0", port=502, unit_id=1)
    await mbs.start()

    # RSSI updater (simple background task)
    asyncio.create_task(rssi_task(model, wifi))

    # CN22 handshake + background tasks
    await driver.handshake()
    asyncio.create_task(driver.keepalive_task())
    asyncio.create_task(driver.poll_task(every_ms=30000))
    asyncio.create_task(driver.event_watch_task())

    log.info("System up. Parking forever.")
    await asyncio.Event().wait()


asyncio.run(main())
