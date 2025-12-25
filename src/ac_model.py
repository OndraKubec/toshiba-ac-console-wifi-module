import uasyncio as asyncio

INT16_MIN = -32768


def _u16(x):
    return x & 0xFFFF


def _s16(x):
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


async def rssi_task(model, wifi, every_ms=10_000):
    while True:
        try:
            rssi = wifi.rssi()
            if rssi is not None:
                model.rssi_dbm = int(rssi)
        except Exception:
            pass
        await asyncio.sleep_ms(every_ms)


class ACModel:
    MODE_AUTO, MODE_COOL, MODE_HEAT, MODE_DRY, MODE_FAN = 0, 1, 2, 3, 4
    FAN_QUIET, FAN_1, FAN_2, FAN_3, FAN_4, FAN_5, FAN_AUTO = 0, 1, 2, 3, 4, 5, 6

    def __init__(self, driver=None):
        self.driver = driver
        self.state = 0
        self.mode = self.MODE_FAN
        self.fanmode = self.FAN_AUTO
        self.swing = 0
        self.setpoint_x10 = 220
        self.roomtemp_x10 = 240
        self.outdoor_x10 = INT16_MIN
        self.rssi_dbm = -58
        self.other = 0x00
        self.p_select = 0x00

    def to_regs(self):
        return [
            self.state,
            self.mode,
            self.fanmode,
            self.swing,
            _u16(self.setpoint_x10),
            _u16(self.roomtemp_x10),
            _u16(self.outdoor_x10 if self.outdoor_x10 is not None else INT16_MIN),
            _u16(self.rssi_dbm),
            _u16(self.other),
            _u16(self.p_select),
        ]

    def apply_write(self, addr, vals):
        """Update local registers and forward to driver asynchronously where applicable."""
        updates = {}
        for i, raw in enumerate(vals):
            a = addr + i
            v = _s16(raw)
            if a == 0:  # power
                if v not in (0, 1):
                    raise ValueError
                updates["state"] = v
            elif a == 1:  # mode
                if v not in (0, 1, 2, 3, 4):
                    raise ValueError
                updates["mode"] = v
            elif a == 2:  # fan
                if v not in (0, 1, 2, 3, 4, 5, 6):
                    raise ValueError
                updates["fanmode"] = v
            elif a == 3:  # swing
                if v not in (0, 1):
                    raise ValueError
                updates["swing"] = v
            elif a == 4:  # setpoint x10
                if v < 200 or v > 300:
                    raise ValueError
                updates["setpoint_x10"] = v
            elif a == 8:  # other (F8 last byte)
                if v < 0 or v > 0xFF:
                    raise ValueError
                updates["other"] = v
            elif a == 9:  # p-select
                if v < 0 or v > 0xFF:
                    raise ValueError
                updates["p_select"] = v
            else:
                pass  # read-only

        if "state" in updates:
            self.state = updates["state"]
        if "mode" in updates:
            self.mode = updates["mode"]
        if "fanmode" in updates:
            self.fanmode = updates["fanmode"]
        if "swing" in updates:
            self.swing = updates["swing"]
        if "setpoint_x10" in updates:
            self.setpoint_x10 = updates["setpoint_x10"]
        if "other" in updates:
            self.other = updates["other"]
        if "p_select" in updates:
            self.p_select = updates["p_select"]

        if not self.driver:
            return

        if "state" in updates:
            asyncio.create_task(self.driver.set_power(self.state))
        if "swing" in updates:
            asyncio.create_task(self.driver.set_swing(self.swing))
        if any(k in updates for k in ("mode", "fanmode", "setpoint_x10", "other")):
            asyncio.create_task(
                self.driver.set_settings(
                    self.mode, self.fanmode, self.setpoint_x10, other=self.other
                )
            )
        if "p_select" in updates:
            asyncio.create_task(self.driver.set_p_select(self.p_select))
