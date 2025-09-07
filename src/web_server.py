# webserver.py
import uasyncio as asyncio
import config

_RESPONSE_OK = (
    b"""HTTP/1.1 200 OK\r
Connection: close\r
Content-Type: text/plain\r
Content-Length: 2\r
\r
OK - """
    + config.DESCRIPTION.encode()
    + b"\r\n"
)


async def _handle(reader, writer):
    try:
        # Read request line
        line = await reader.readline()
        if not line:
            await writer.aclose()
            return
        # Drain and ignore headers until blank line
        while True:
            h = await reader.readline()
            if not h or h == b"\r\n":
                break

        print("Web server:", line)

        await writer.awrite(_RESPONSE_OK)
    finally:
        try:
            await writer.aclose()
        except Exception:
            pass


async def start(host="0.0.0.0", port=80):
    """Start the server (non-blocking). Returns the server object."""
    srv = await asyncio.start_server(_handle, host, port)
    print("HTTP server on http://%s:%d (returns 'OK')" % (host, port))
    return srv


async def stop(server):
    """Gracefully stop a server started with start()."""
    server.close()
    await server.wait_closed()
