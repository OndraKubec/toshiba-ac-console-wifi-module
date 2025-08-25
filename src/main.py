import uasyncio as asyncio
import wifi
import web_server
from modbus_tcp import ModbusTCPServer, ACModel, simulate_sensors


async def main():
    # Connect to wifi
    ip = wifi.connect()

    # Start web server (non-blocking)
    await web_server.start(host=ip, port=80)

    # create fake AC model
    model = ACModel()

    # create and start modbus tcp server
    server = ModbusTCPServer(model, host=ip, port=502, unit_id=1)
    await server.start()

    # simulate sensors
    asyncio.create_task(simulate_sensors(model))

    # Keep the loop alive; replace with your own app tasks
    await asyncio.Event().wait()


asyncio.run(main())
