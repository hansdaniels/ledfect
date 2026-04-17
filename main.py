import machine
import time

import boot_log
import slot_manager


ENV_FILE = ".env"
BOOT_SETUP_HOLD_MS = 800
BOOT_INDICATOR_BLINK_MS = 120


def show_boot_indicator():
    try:
        led = machine.Pin("LED", machine.Pin.OUT)
    except Exception:
        return

    for _ in range(3):
        led.value(1)
        time.sleep_ms(BOOT_INDICATOR_BLINK_MS)
        led.value(0)
        time.sleep_ms(BOOT_INDICATOR_BLINK_MS)


def should_reset_update_secret():
    pin = machine.Pin(9, machine.Pin.IN, machine.Pin.PULL_UP)
    time.sleep_ms(300)

    if pin.value() != 0:
        return False

    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < BOOT_SETUP_HOLD_MS:
        if pin.value() != 0:
            return False
        time.sleep_ms(20)
    return pin.value() == 0


def load_env():
    env = {}
    try:
        with open(ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env[key.strip()] = value.strip().strip("'").strip('"')
    except OSError:
        pass
    return env


def save_env(env):
    with open(ENV_FILE, "w") as f:
        for key in sorted(env):
            f.write("{}={}\n".format(key, env[key]))


def reset_update_secret_to_default():
    env = load_env()
    env["UPDATE_SECRET"] = "default_secret"
    save_env(env)
    print("Boot recovery: UPDATE_SECRET reset to default_secret")


def import_slot_runner(slot_name):
    module_name = "{}.app_main".format(slot_name)
    module = __import__(module_name, None, None, ("run",))
    return module.run


def fatal_reset():
    try:
        led = machine.Pin("LED", machine.Pin.OUT)
        for _ in range(10):
            led.value(1)
            time.sleep_ms(80)
            led.value(0)
            time.sleep_ms(80)
    except Exception:
        pass
    time.sleep_ms(3000)
    machine.reset()


try:
    print("Boot: starting main.py")
    boot_log.log("root main.py start")
    show_boot_indicator()
    if should_reset_update_secret():
        reset_update_secret_to_default()
        boot_log.log("update secret reset to default")

    slot_name, _ = slot_manager.choose_boot_slot()
    print("Boot slot: {}".format(slot_name))
    boot_log.log("boot slot {}".format(slot_name))
    run_slot = import_slot_runner(slot_name)
    run_slot(slot_name=slot_name)
except Exception as e:
    print("FATAL ERROR: {}".format(e))
    boot_log.log("root fatal {}".format(e))
    fatal_reset()
