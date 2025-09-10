import uasyncio as asyncio
import config
from logger import Logger


class WebServer:
    def __init__(self, host="0.0.0.0", port=80, description=None):
        self.host = host
        self.port = port
        self.description = (
            description if description is not None else config.DESCRIPTION
        )
        self._server = None
        self.log = Logger("webserver")

        # Body as UTF-8 bytes (so non-ASCII in description works)
        body_txt = "OK - " + self.description + "\r\n"
        body = body_txt.encode("utf-8")

        # Headers are ASCII; declare charset so clients render UTF-8 correctly
        headers_txt = (
            "HTTP/1.1 200 OK\r\n"
            "Connection: close\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "Content-Length: %d\r\n"
            "\r\n"
        ) % len(body)

        self._headers_ok = headers_txt.encode("ascii")
        self._response_ok = self._headers_ok + body

    async def _handle(self, reader, writer):
        try:
            # Request line
            line = await reader.readline()
            if not line:
                await writer.aclose()
                return

            # Drain headers
            while True:
                h = await reader.readline()
                if not h or h == b"\r\n":
                    break

            # Log request line safely
            try:
                self.log.debug(
                    ("HTTP request: " + line.decode("latin-1", "replace")).strip()
                )
            except Exception:
                pass

            method = None
            try:
                p = line.split()
                if p:
                    method = p[0].decode("ascii", "ignore").upper()
            except Exception:
                method = None

            if method == "HEAD":
                await writer.awrite(self._headers_ok)
            else:
                await writer.awrite(self._response_ok)

        finally:
            try:
                await writer.aclose()
            except Exception:
                pass

    async def start(self):
        if self._server is not None:
            self.log.warning("Server already running on %s:%d" % (self.host, self.port))
            return self._server
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.log.info("HTTP server on http://%s:%d" % (self.host, self.port))
        return self._server

    async def stop(self):
        if self._server is None:
            self.log.warning("No server to stop")
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self.log.info("Server stopped")
