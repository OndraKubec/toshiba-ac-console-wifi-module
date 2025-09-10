import uasyncio as asyncio
import config
from logger import Logger


class WebServer:
    def __init__(self, host="0.0.0.0", port=80, description=None):
        self.host = host
        self.port = port
        desc = description if description is not None else config.DESCRIPTION

        body = b"OK - " + desc.encode() + b"\r\n"
        self._response_ok = (
            b"HTTP/1.1 200 OK\r\n"
            b"Connection: close\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"\r\n" + body
        )

        self._server = None
        self.log = Logger("webserver")

    async def _handle(self, reader, writer):
        try:
            # Read request line
            line = await reader.readline()
            if not line:
                await writer.aclose()
                return

            # Drain headers until blank line
            while True:
                h = await reader.readline()
                if not h or h == b"\r\n":
                    break

            self.log.debug(f"HTTP request: {line.decode().strip()}")
            await writer.awrite(self._response_ok)
        finally:
            try:
                await writer.aclose()
            except Exception:
                pass

    async def start(self):
        if self._server is not None:
            self.log.warning(f"Server already running on {self.host}:{self.port}")
            return self._server

        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.log.info(f"HTTP server on http://{self.host}:{self.port} (returns 'OK')")
        return self._server

    async def stop(self):
        if self._server is None:
            self.log.warning("No server to stop")
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self.log.info("Server stopped")
