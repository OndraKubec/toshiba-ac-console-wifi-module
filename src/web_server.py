import uasyncio as asyncio
import config


class WebServer:
    def __init__(self, host="0.0.0.0", port=80, description=None):
        self.host = host
        self.port = port
        self.description = (
            description if description is not None else config.DESCRIPTION
        )
        self._server = None
        self._update_response()

    def _update_response(self):
        body = b"OK - " + self.description.encode() + b"\r\n"
        # Minimal HTTP 1.1 response
        self._response_ok = (
            b"HTTP/1.1 200 OK\r\n"
            b"Connection: close\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"\r\n" + body
        )

    def set_description(self, description: str):
        """Update the description shown in the response body."""
        self.description = description
        self._update_response()

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

            print("Web server:", line)
            await writer.awrite(self._response_ok)
        finally:
            try:
                await writer.aclose()
            except Exception:
                pass

    async def start(self):
        """Start the server (non-blocking)."""
        if self._server is not None:
            print("[web] Server already running on %s:%d" % (self.host, self.port))
            return self._server

        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        print("HTTP server on http://%s:%d (returns 'OK')" % (self.host, self.port))
        return self._server

    async def stop(self):
        """Gracefully stop a previously started server."""
        if self._server is None:
            print("[web] No server to stop.")
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        print("[web] Server stopped.")
