import time
import config


class Logger:
    LEVELS = {
        "DEBUG": 10,
        "INFO": 20,
        "WARNING": 30,
        "ERROR": 40,
    }

    def __init__(self, name: str, level: str = config.LOG_LEVEL):
        self.name = name
        self.level = self.LEVELS.get(level.upper(), 20)

    def set_level(self, level: str):
        self.level = self.LEVELS.get(level.upper(), self.level)

    def _log(self, level_name: str, msg: str):
        if self.LEVELS[level_name] < self.level:
            return
        ms = time.ticks_ms()
        sec = ms // 1000
        ms = ms % 1000
        uptime = f"{sec:6}.{ms:03d}s"
        level_str = f"[{level_name}]".ljust(9)
        name_str = f"{self.name:<10.10}"
        print(f"{uptime} {level_str} {name_str}: {msg}")

    def debug(self, msg: str):
        self._log("DEBUG", msg)

    def info(self, msg: str):
        self._log("INFO", msg)

    def warning(self, msg: str):
        self._log("WARNING", msg)

    def error(self, msg: str):
        self._log("ERROR", msg)
