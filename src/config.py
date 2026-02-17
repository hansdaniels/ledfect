import ujson
import uasyncio as asyncio
import time

CONFIG_FILE = "config.json"

class ConfigManager:
    def __init__(self):
        self.config = {}
        self._dirty = False
        self._last_change_time = 0
        self.debounce_ms = 5000
        self.load()

    def load(self):
        try:
            with open(CONFIG_FILE, "r") as f:
                self.config = ujson.load(f)
            print("Config loaded.")
        except (OSError, ValueError):
            print("Config file not found or invalid, starting with defaults.")
            self.config = {}

    def save(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                ujson.dump(self.config, f)
            print("Config saved.")
            self._dirty = False
        except Exception as e:
            print(f"Error saving config: {e}")

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        if self.config.get(key) != value:
            self.config[key] = value
            self._dirty = True
            self._last_change_time = time.ticks_ms()

    async def auto_save_loop(self):
        while True:
            if self._dirty:
                now = time.ticks_ms()
                diff = time.ticks_diff(now, self._last_change_time)
                if diff > self.debounce_ms:
                    self.save()
            await asyncio.sleep_ms(1000)

    def force_save(self):
        if self._dirty:
            self.save()
