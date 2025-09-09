import network, time
import uasyncio as asyncio
import config


class WiFiManager:
    def __init__(
        self, ssid=config.WIFISSID, pwd=config.WIFIPWD, hostname=config.HOSTNAME
    ):
        self._ssid = ssid
        self._pwd = pwd
        self._hostname = hostname
        self._wlan = network.WLAN(network.STA_IF)
        self._wlan.active(True)
        self._wlan.config(dhcp_hostname=self._hostname)

    def connect(self, timeout_ms=15000):
        if not self._wlan.isconnected():
            print("[wifi] Connecting…")
            self._wlan.connect(self._ssid, self._pwd)
            t0 = time.ticks_ms()
            while not self._wlan.isconnected():
                if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                    raise RuntimeError("Wi-Fi timeout")
                time.sleep(0.2)
        ip = self.ip()
        print("[wifi] Connected, IP:", ip)
        return ip

    def is_connected(self):
        return self._wlan.isconnected()

    def ip(self):
        return self._wlan.ifconfig()[0] if self.is_connected() else None

    async def keepalive(self, retry_base=1, retry_max=30):
        backoff = retry_base
        last_ip = self.ip()
        while True:
            if not self.is_connected():
                print("[wifi] Link lost, reconnecting…")
                try:
                    self._wlan.connect(self._ssid, self._pwd)
                    t0 = time.ticks_ms()
                    while not self._wlan.isconnected():
                        if time.ticks_diff(time.ticks_ms(), t0) > 15000:
                            print("[wifi] Reconnect attempt timed out.")
                            break
                        await asyncio.sleep(0.2)
                except Exception as e:
                    print("[wifi] Reconnect error:", e)

                if self.is_connected():
                    backoff = retry_base
                    new_ip = self.ip()
                    print("[wifi] Reconnected, IP:", new_ip)
                    if new_ip != last_ip:
                        print("[wifi] IP changed from", last_ip, "to", new_ip)
                    last_ip = new_ip
                else:
                    backoff = min(backoff * 2, retry_max)
                    await asyncio.sleep(backoff)
            else:
                await asyncio.sleep(5)
