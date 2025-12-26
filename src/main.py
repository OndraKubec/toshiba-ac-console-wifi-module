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
    wifi.connect()
    asyncio.create_task(wifi.keepalive())

    # HTTP server (logs inside WebServer)
    web = WebServer(host="0.0.0.0", port=80)
    await web.start()

    # Wait for the console unit to get ready
    await asyncio.sleep(20)

    # Model + driver (driver logs handshake/traffic when debug enabled)
    model = ACModel(driver=None)
    driver = ToshibaCN22Driver(model)
    model.driver = driver

    # CN22 handshake
    await driver.handshake()

    # Modbus TCP (bind all interfaces to survive DHCP IP changes)
    mbs = ModbusTCPServer(model, host="0.0.0.0", port=502, unit_id=1)
    await mbs.start()

    #  Background tasks
    # asyncio.create_task(driver.keepalive_task()) # disabled due to enought polling to have session alive
    asyncio.create_task(driver.poll_task(every_ms=15000))
    asyncio.create_task(driver.event_watch_task())
    asyncio.create_task(rssi_task(model, wifi))

    log.info("System up. Parking forever.")
    await asyncio.Event().wait()


asyncio.run(main())
