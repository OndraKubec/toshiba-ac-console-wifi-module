import network
import time
import config


def connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("Connecting Wi-Fi...")
        wlan.connect(config.WIFISSID, config.WIFIPWD)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > 15000:
                raise RuntimeError("Wi-Fi timeout")
            time.sleep(0.2)
    ip = wlan.ifconfig()[0]
    print("Connected to Wi-Fi, IP:", ip)
    return ip
