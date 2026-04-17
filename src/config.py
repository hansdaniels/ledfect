import ujson
import uasyncio as asyncio
import time

CONFIG_FILE = "config.json"
ENV_FILE = ".env"

class ConfigManager:
    def __init__(self):
        self.config = {}
        self.env = {}
        self._dirty = False
        self._last_change_time = 0
        self.debounce_ms = 5000
        self.load()
        self.load_env()

    def load_env(self):
        self.env = {}
        try:
            with open(ENV_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        # Remove quotes if present
                        v = v.strip().strip("'").strip('"')
                        self.env[k.strip()] = v
            print("Environment variables loaded from .env")
        except OSError:
            print("No .env file found, using defaults if applicable.")

    def save_env(self):
        try:
            with open(ENV_FILE, "w") as f:
                for key in sorted(self.env):
                    f.write("{}={}\n".format(key, self.env[key]))
            print("Environment variables saved to .env")
        except Exception as e:
            print("Error saving .env: {}".format(e))

    def set_env(self, key, value, persist=True):
        self.env[key] = value
        if persist:
            self.save_env()

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
