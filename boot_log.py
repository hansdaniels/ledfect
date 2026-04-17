import time

LOG_FILE = "boot.log"
MAX_LINES = 80
CONFIG_FILE = "config.json"


def enabled():
    try:
        import ujson as json_mod
    except ImportError:
        import json as json_mod

    try:
        with open(CONFIG_FILE, "r") as f:
            config = json_mod.load(f)
        return bool(config.get("debug_boot_log", False))
    except Exception:
        return False


def log(message):
    if not enabled():
        return
    line = "{} {}\n".format(time.time(), message)
    try:
        existing = []
        try:
            with open(LOG_FILE, "r") as f:
                existing = f.readlines()
        except OSError:
            existing = []

        existing.append(line)
        if len(existing) > MAX_LINES:
            existing = existing[-MAX_LINES:]

        with open(LOG_FILE, "w") as f:
            for item in existing:
                f.write(item)
    except Exception:
        pass


def clear():
    try:
        with open(LOG_FILE, "w") as f:
            f.write("")
    except Exception:
        pass
