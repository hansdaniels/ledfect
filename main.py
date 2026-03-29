import uasyncio as asyncio
import time
import _thread
import machine
from src.config import ConfigManager
from src.hardware import StripController, Button, PIRSensor, IRReceiver, LightSensor, Buzzer
from src.effects import SolidColorEffect, LarsonScannerEffect, WanderingSpotsEffect, SparkleEffect, RainbowEffect, PulseEffect, LavaLampEffect, FadingSparkleEffect
from src.web_server import WebServer
from src.wifi_manager import WiFiManager
from src.utils import scale_buffer
import gc

# Pin Definitions
PIN_LEDS = 22
PIN_BTN_R = 9
PIN_BTN_C = 12
PIN_PIR = 15
PIN_IR = 14
PIN_LIGHT = 21
PIN_BUZZER = 19

NUM_LEDS = 300
PIR_TIMEOUT_SECONDS = 5 * 60
NIGHT_EFFECT_NAME = "FadingSparkle"
NIGHT_BRIGHTNESS = 18
LIGHT_SENSOR_DARK_VALUE = 1
LIGHT_TRIGGER_COUNT = 3
LIGHT_RELEASE_COUNT = 8

# Gamma-corrected brightness steps for visually linear changes
BRIGHTNESS_STEPS = [
    0, 1, 2, 4, 8, 12, 18, 25, 34, 45, 
    57, 71, 86, 104, 124, 145, 169, 195, 224, 255
]

IR_COLOR_PRESETS = {
    "1": (255, 0, 0),
    "2": (255, 128, 0),
    "3": (255, 255, 0),
    "4": (0, 255, 0),
    "5": (0, 255, 255),
    "6": (0, 0, 255),
    "7": (255, 0, 255),
    "8": (128, 0, 255),
    "9": (255, 255, 255),
}

class App:
    def __init__(self, config=None):
        self.config = config or ConfigManager()
        
        # Hardware
        self.strip = StripController(PIN_LEDS, NUM_LEDS)
        self.btn_r = Button(PIN_BTN_R, "Next")
        self.btn_c = Button(PIN_BTN_C, "Mode")
        self.pir = PIRSensor(PIN_PIR)
        self.ir = IRReceiver(PIN_IR)
        self.light = LightSensor(PIN_LIGHT)
        self.buzzer = Buzzer(PIN_BUZZER)
        
        # Logic
        self.buffer = bytearray(NUM_LEDS * 3)
        self.blank_buffer = bytearray(NUM_LEDS * 3)
        self._last_written_buffer = bytearray(NUM_LEDS * 3)
        self.current_effect = None
        self.current_effect_name = self.config.get("effect", "SolidColor")
        self.brightness = self.config.get("brightness", 255)
        self.pir_enabled = self.config.get("pir_enabled", True)
        self.pir_timeout_enabled = self.config.get("pir_timeout_enabled", False)
        self.is_paused = False
        self.last_motion_time = time.time()
        self.is_off_due_to_timeout = False
        self.is_off_manual = False
        self.manual_off_brightness_cache = 255
        self.speed_scaler = self.config.get("speed_scaler", 1.0)  # Master speed control (1.0 = 100%)
        self.night_mode_armed = self.config.get("night_mode_armed", False)
        self.night_mode_active = False
        self.night_effect_name = self.config.get("night_effect", NIGHT_EFFECT_NAME)
        self.night_brightness = self.config.get("night_brightness", NIGHT_BRIGHTNESS)
        self.light_dark_value = self.config.get("light_dark_value", LIGHT_SENSOR_DARK_VALUE)
        self._light_dark_counter = 0
        self._light_bright_counter = 0
        self._saved_effect_name = None
        self._saved_brightness = None
        
        # Load initial effect
        self._load_effect(self.current_effect_name)
        
        # Web Server
        self.server = WebServer(self)
        
        # Second Core Render Thread
        self._render_buffer = bytearray(NUM_LEDS * 3)
        self._render_ready = False
        self._shutdown_render = False
        self._has_written_frame = False
        _thread.start_new_thread(self._render_worker, ())

    def _render_worker(self):
        # This function runs infinitely on the SECOND CPU CORE.
        # It handles writing to the NeoPixel strip, which disables its local core's interrupts.
        # This leaves Core 0 completely free to handle IR and asyncio without drops!
        while not self._shutdown_render:
            if self._render_ready:
                self.strip.write(self._render_buffer)
                self._render_ready = False
            else:
                # Sleep briefly to yield core
                time.sleep_ms(2)

    def reset_activity(self):
        """Reset the user activity timer (wakes up if sleeping)."""
        self.last_motion_time = time.time()
        if self.is_off_due_to_timeout:
            print("Activity detected! Waking up.")
            self.is_off_due_to_timeout = False

    def _trigger_beep(self, duration_ms=50):
        if self.buzzer.is_enabled():
            asyncio.create_task(self.buzzer.beep(duration_ms=duration_ms))

    def _set_manual_off(self, value):
        self.is_off_manual = value
        if self.is_off_manual:
            print("Manual Off")
            self._trigger_beep(120)
        else:
            print("Manual On")
            self._trigger_beep(70)

    def _set_night_mode_armed(self, value):
        armed = bool(value)
        if self.night_mode_armed == armed:
            return
        self.night_mode_armed = armed
        self.config.set("night_mode_armed", armed)
        if not armed and self.night_mode_active:
            self._deactivate_night_mode()
        print("Night mode armed" if armed else "Night mode disarmed")
        self._trigger_beep(40 if armed else 120)

    def _activate_night_mode(self):
        if self.night_mode_active:
            return
        self.night_mode_active = True
        self._saved_effect_name = self.current_effect_name
        self._saved_brightness = self.brightness
        self._load_effect(self.night_effect_name)
        self.brightness = self.night_brightness
        self.config.set("brightness", self.brightness)
        print("Night mode active")

    def _deactivate_night_mode(self):
        if not self.night_mode_active:
            return
        self.night_mode_active = False
        restore_effect = self._saved_effect_name or self.config.get("effect", "SolidColor")
        restore_brightness = self._saved_brightness
        self._saved_effect_name = None
        self._saved_brightness = None
        self._load_effect(restore_effect)
        if restore_brightness is not None:
            self.brightness = restore_brightness
            self.config.set("brightness", self.brightness)
        print("Night mode inactive")

    def _should_output_light(self):
        if self.is_off_due_to_timeout or self.is_off_manual:
            return False
        return True

    def _save_effect_state(self):
        if not self.current_effect:
            return
        states = self.config.get("effect_states", {})
        if not isinstance(states, dict):
            states = {}
            
        states[self.current_effect_name] = self.current_effect.get_state()
        self.config.config["effect_states"] = states
        self.config._dirty = True
        import time
        self.config._last_change_time = time.ticks_ms()

    def _load_effect(self, name):
        self.current_effect_name = name
        
        effect = None
        if name == "SolidColor":
            effect = SolidColorEffect(NUM_LEDS, color=(255, 60, 0))
        elif name == "LarsonScanner":
            effect = LarsonScannerEffect(NUM_LEDS)
        elif name == "WanderingSpots":
            effect = WanderingSpotsEffect(NUM_LEDS)
        elif name == "Sparkle":
            effect = SparkleEffect(NUM_LEDS)
        elif name == "Rainbow":
            effect = RainbowEffect(NUM_LEDS)
        elif name == "Pulse":
            effect = PulseEffect(NUM_LEDS)
        elif name == "LavaLamp":
            effect = LavaLampEffect(NUM_LEDS)
        elif name == "FadingSparkle":
            effect = FadingSparkleEffect(NUM_LEDS)
        else:
            effect = SolidColorEffect(NUM_LEDS, color=(50,50,50)) # Fallback
            
        self.current_effect = effect
        
        # Restore saved state if exists
        states = self.config.get("effect_states", {})
        if isinstance(states, dict) and name in states:
            self.current_effect.set_state(states[name])
            
        self.config.set("effect", name)

    def get_status(self):
        return {
            "effect": self.current_effect_name,
            "brightness": self.brightness,
            "pir_enabled": self.pir_enabled,
            "pir_timeout_enabled": self.pir_timeout_enabled,
            "night_mode_armed": self.night_mode_armed,
            "night_mode_active": self.night_mode_active,
            "motion_timeout": self.is_off_due_to_timeout,
            "fps": 0 # TODO measure fps
        }

    def update_config(self, data):
        self.reset_activity()
        if "effect" in data:
            self._load_effect(data["effect"])
        if "brightness" in data:
            self.brightness = int(data["brightness"])
            self.config.set("brightness", self.brightness)
        if "pir_enabled" in data:
            self.pir_enabled = bool(data["pir_enabled"])
            self.config.set("pir_enabled", self.pir_enabled)
            if self.pir_enabled:
                self.reset_activity()
            else:
                self.is_off_due_to_timeout = False
        if "pir_timeout_enabled" in data:
            self.pir_timeout_enabled = bool(data["pir_timeout_enabled"])
            self.config.set("pir_timeout_enabled", self.pir_timeout_enabled)
            if self.pir_timeout_enabled:
                self.reset_activity()
            else:
                self.is_off_due_to_timeout = False
        if "night_mode_armed" in data:
            self._set_night_mode_armed(data["night_mode_armed"])

    def _apply_color_to_current_effect(self, color):
        effect = self.current_effect
        if effect is None:
            return False

        if hasattr(effect, "set_color"):
            effect.set_color(color)
            return True

        # Multi-instance effects: apply to the most recently added instance.
        if hasattr(effect, "scanners") and effect.scanners:
            effect.scanners[-1]["color"] = color
            return True

        if hasattr(effect, "spots") and effect.spots:
            effect.spots[-1].color = color
            return True

        if hasattr(effect, "blobs") and effect.blobs:
            effect.blobs[-1].color = color
            return True

        params = getattr(effect, "params", None)
        if isinstance(params, dict) and "color" in params:
            params["color"] = color
            if "kelvin" in params:
                params["kelvin"] = None
            return True

        return False

    async def run(self):
        # Start loops
        asyncio.create_task(self.server.start())
        asyncio.create_task(self.config.auto_save_loop())
        asyncio.create_task(self.input_loop())
        asyncio.create_task(self.pir_loop())
        asyncio.create_task(self.light_sensor_loop())
        
        # Main Animation Loop
        print("Starting Animation Loop")
        frame_counter = 0
        
        # Optionally set a gc threshold so pauses are shorter if they happen
        gc.threshold(32768) 
        
        self.logical_time = 0
        last_t = time.ticks_ms()
        
        while True:
            t0 = time.ticks_ms()
            dt_real = time.ticks_diff(t0, last_t)
            last_t = t0
            
            # Logic Update (Only render if ON)
            is_on = self._should_output_light()
            
            if is_on:
                if self.current_effect:
                    if not getattr(self, 'is_paused', False):
                        # Apply speed_scaler globally by accelerating the logical clock!
                        self.logical_time += dt_real * getattr(self, 'speed_scaler', 1.0)
                        
                    self.current_effect.update(self.logical_time)
                    self.buffer[:] = self.blank_buffer
                    self.current_effect.render(self.buffer)
                
                # Apply Global Brightness
                final_scale = self.brightness / 255.0
                
                if final_scale < 0.99:
                    scale_buffer(self.buffer, final_scale)
                
                # Check if the buffer actually changed before writing.
                if (not self._has_written_frame) or self.buffer != self._last_written_buffer:
                    # Handoff to Second Core
                    # We only signal the thread if it's currently waiting (flag is False)
                    # If it's already busy writing the previous frame, we just skip (frame drop)
                    # to maintain timing!
                    if not self._render_ready:
                        self._render_buffer[:] = self.buffer
                        self._render_ready = True # Signals thread to go!
                        self._last_written_buffer[:] = self.buffer
                        self._has_written_frame = True
                    
                self._is_black_written = False
            else:
                # Off state
                if getattr(self, '_is_black_written', False) == False:
                    if not self._render_ready:
                        self._render_buffer[:] = self.blank_buffer
                        self._render_ready = True
                        self._is_black_written = True

            # Frame pacing
            t1 = time.ticks_ms()
            diff = time.ticks_diff(t1, t0)
            wait = max(0, 16 - diff) # ~60fps cap
            await asyncio.sleep_ms(wait)

    async def input_loop(self):
        print("Input Loop Started")
        effects = ["SolidColor", "LarsonScanner", "WanderingSpots", "Sparkle", "Rainbow", "Pulse", "LavaLamp", "FadingSparkle"]
        cnt = 0
        while True:
            cnt += 1
            if cnt % 100 == 0:
                print("Input Loop Alive")
            
            # Buttons
            if self.btn_r.check() == 1:
                self.reset_activity()
                print("Button Pressed - Next Effect")
                try:
                    idx = effects.index(self.current_effect_name)
                    next_idx = (idx + 1) % len(effects)
                    print(f"Switching to {effects[next_idx]}")
                    self._load_effect(effects[next_idx])
                except ValueError:
                    self._load_effect(effects[0])
            
            
            # Button Center (Event Based)
            evt_c = self.btn_c.check()
            if evt_c > 0: self.reset_activity()
            
            if evt_c == 1:
                # Short Press: Randomize
                print("Button Center Short: Randomize")
                if self.current_effect:
                    self.current_effect.randomize()
                    self._save_effect_state()
            elif evt_c == 2:
                # Long Press: Toggle On/Off
                print("Button Center Long: Toggle On/Off")
                self._set_manual_off(not self.is_off_manual)

            # IR Remote
            code = self.ir.get_code()
            if code is not None:
                print(f"IR Remote - Received Code: {code} (Hex: 0x{code:02X})")
                self.reset_activity()
                
                # Map specific codes to actions using a dictionary.
                # YOUR REMOTE IS INCREDIBLE! It uses a rare Toggle-Bit protocol where it alternates
                # every button press between a Base Code (e.g. 0xD1) and a 1-bit Circular Left Shift
                # of the entire 32-bit packet (which becomes 0xA2)!
                # Rather than making you write both, we can just define the base codes and
                # let Python check for both!
                
                def get_toggle_code(base_cmd):
                    # For example, if Base CMD is 0xD1 (209) and Addr is 0x80 (128):
                    # The full 32-bit packet is 0x807FD12E
                    # Shifted Left 1 bit = 0x00FFA25D -> New CMD is 0xA2 (162)
                    packet = (0x80 << 24) | (0x7F << 16) | (base_cmd << 8) | ((~base_cmd) & 0xFF)
                    # Circular shift left by 1
                    shifted = ((packet << 1) & 0xFFFFFFFF) | (packet >> 31)
                    return (shifted >> 8) & 0xFF

                def build_map(*base_cmds):
                    # Automatically creates a list of [Base, Toggle] for every command
                    return [c for cmd in base_cmds for c in (cmd, get_toggle_code(cmd))]
                
                
                # List of all available effects for cycling
                effects = [
                    "SolidColor", "LarsonScanner", "WanderingSpots", 
                    "Sparkle", "Rainbow", "Pulse", "LavaLamp", "FadingSparkle"
                ]

                ir_mapping = {
                    "VOL-":       build_map(0xD1),   
                    "PLAY/PAUSE": build_map(0xB1),   
                    "VOL+":       build_map(0xF1),   
                    "SETUP":      build_map(0x22),   
                    "UP":         build_map(0x81),   
                    "STOP/MODE":  build_map(0xE1),   
                    "LEFT":       build_map(0xE0),   # E0 (Left)
                    "ENTER/SAVE": build_map(0xA8),   
                    "RIGHT":      build_map(0x90),   # 90 (Right)
                    "0_10+":      build_map(0xB4),   
                    "DOWN":       build_map(0xCC),   
                    "BACK":       build_map(0xD8),   
                    "BACK_LONG":  [c + 0x1000 for c in build_map(0xD8)],
                    "1":          build_map(0x30),
                    "2":          build_map(0x18),
                    "3":          build_map(0x7A),
                    "4":          build_map(0x10),
                    "5":          build_map(0x9C),
                    "6":          build_map(0xAD),
                    "7":          build_map(0x42),
                    "8":          build_map(0x4A),
                    "9":          build_map(0xA9),
                }

                color_key = None
                for key in IR_COLOR_PRESETS:
                    if code in ir_mapping[key]:
                        color_key = key
                        break

                if color_key is not None:
                    color = IR_COLOR_PRESETS[color_key]
                    if self._apply_color_to_current_effect(color):
                        self._save_effect_state()
                        print(f"Applied IR color {color_key}: {color}")
                    else:
                        print(f"Current effect does not support direct color changes: {self.current_effect_name}")
                elif code in ir_mapping["STOP/MODE"]:
                    self._set_manual_off(not self.is_off_manual)

                elif code in ir_mapping["SETUP"]:
                    self.pir_timeout_enabled = not self.pir_timeout_enabled
                    self.config.set("pir_timeout_enabled", self.pir_timeout_enabled)
                    if self.pir_timeout_enabled:
                        self.reset_activity()
                        print("PIR timeout enabled (5 min)")
                    else:
                        self.is_off_due_to_timeout = False
                        print("PIR timeout disabled")

                elif code in ir_mapping["ENTER/SAVE"]:
                    self._set_night_mode_armed(not self.night_mode_armed)
                         
                elif code in ir_mapping["UP"]: # Up = Next Effect
                    try:
                        idx = effects.index(self.current_effect_name)
                        next_idx = (idx + 1) % len(effects)
                        print(f"Switching Effect via UP to: {effects[next_idx]}")
                        self._load_effect(effects[next_idx])
                    except ValueError:
                        self._load_effect(effects[0])
                    
                elif code in ir_mapping["DOWN"]: # Down = Prev Effect
                    try:
                        idx = effects.index(self.current_effect_name)
                        next_idx = (idx - 1) % len(effects)
                        print(f"Switching Effect via DOWN to: {effects[next_idx]}")
                        self._load_effect(effects[next_idx])
                    except ValueError:
                        self._load_effect(effects[0])
                    
                elif code in ir_mapping["RIGHT"]: # Right = Faster
                    self.speed_scaler = min(5.0, self.speed_scaler + 0.25)
                    self.config.set("speed_scaler", self.speed_scaler)
                    print(f"Global Speed Increased: {self.speed_scaler:.2f}x")
                    
                elif code in ir_mapping["LEFT"]: # Left = Slower
                    self.speed_scaler = max(0.1, self.speed_scaler - 0.25)
                    self.config.set("speed_scaler", self.speed_scaler)
                    print(f"Global Speed Decreased: {self.speed_scaler:.2f}x")
                    
                elif code in ir_mapping["PLAY/PAUSE"]:
                    self.is_paused = not getattr(self, 'is_paused', False)
                    print(f"Effect Paused: {self.is_paused}")
                        
                elif code in ir_mapping["VOL+"]: # Vol+ = Brightness Up
                    next_steps = [b for b in BRIGHTNESS_STEPS if b > self.brightness]
                    self.brightness = next_steps[0] if next_steps else 255
                    self.config.set("brightness", self.brightness)
                    print(f"Brightness UP: {self.brightness}/255")
                    
                elif code in ir_mapping["VOL-"]: # Vol- = Brightness Down
                    prev_steps = [b for b in BRIGHTNESS_STEPS if b < self.brightness]
                    self.brightness = prev_steps[-1] if prev_steps else 0
                    self.config.set("brightness", self.brightness)
                    print(f"Brightness DOWN: {self.brightness}/255")

                elif code in ir_mapping["0_10+"]:
                    if hasattr(self.current_effect, 'add_instance'):
                        self.current_effect.add_instance()
                        self._save_effect_state()
                        print("Added effect instance / Increased value")
                        
                elif code in ir_mapping["BACK"]:
                    if hasattr(self.current_effect, 'remove_instance'):
                        self.current_effect.remove_instance()
                        self._save_effect_state()
                        print("Removed effect instance / Decreased value")
                        
                elif code in ir_mapping["BACK_LONG"]:
                    print("Long press BACK detected! Resetting current effect.")
                    self._trigger_beep(150)
                    states = self.config.get("effect_states", {})
                    if self.current_effect_name in states:
                        del states[self.current_effect_name]
                        self.config.config["effect_states"] = states
                        self.config._dirty = True
                        import time
                        self.config._last_change_time = time.ticks_ms()
                        self.config.save()
                    self._load_effect(self.current_effect_name)

            await asyncio.sleep_ms(50)

    async def pir_loop(self):
        while True:
            if self.pir_enabled and self.pir.check():
                # Motion detected
                self.reset_activity()
            
            # Check timeout
            if self.pir_timeout_enabled and not self.is_off_due_to_timeout:
                if time.time() - self.last_motion_time > PIR_TIMEOUT_SECONDS:
                    print("No motion for 5m. Turning off.")
                    self.is_off_due_to_timeout = True
                    # Clear strip
                    self.strip.write(bytearray(NUM_LEDS * 3)) # Black
            elif not self.pir_timeout_enabled and self.is_off_due_to_timeout:
                self.is_off_due_to_timeout = False
            
            await asyncio.sleep_ms(1000)

    async def light_sensor_loop(self):
        while True:
            val = self.light.read()
            is_dark = val == self.light_dark_value

            if is_dark:
                self._light_dark_counter += 1
                self._light_bright_counter = 0
            else:
                self._light_bright_counter += 1
                self._light_dark_counter = 0

            if self.night_mode_armed and not self.night_mode_active and self._light_dark_counter >= LIGHT_TRIGGER_COUNT:
                self._activate_night_mode()
            elif self.night_mode_active and self._light_bright_counter >= LIGHT_RELEASE_COUNT:
                self._deactivate_night_mode()

            print(
                f"Lichtsensor (GP{PIN_LIGHT}): {val} dark={is_dark} "
                f"armed={self.night_mode_armed} active={self.night_mode_active}"
            )
            await asyncio.sleep(2) # 2 seconds

app_instance = None
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


def should_start_wifi_setup():
    pin = machine.Pin(PIN_BTN_C, machine.Pin.IN, machine.Pin.PULL_UP)
    # Give the input a moment to settle after power-up before treating it
    # as an intentional long press.
    time.sleep_ms(300)

    if pin.value() != 0:
        return False

    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < BOOT_SETUP_HOLD_MS:
        if pin.value() != 0:
            return False
        time.sleep_ms(20)
    return pin.value() == 0


async def bootstrap_wifi(config):
    wifi = WiFiManager(config)
    if should_start_wifi_setup():
        print("Center button held during boot. Starting Wi-Fi setup portal.")
        await wifi.run_setup_portal()
        return wifi

    if wifi.has_saved_credentials():
        if not await wifi.connect_saved():
            print("Wi-Fi startup connection failed. Flashing SOS on onboard LED.")
            await wifi.flash_sos(2)
    return wifi

async def _wifi_bg(config):
    # Small delay so at least a few frames render before WiFi blocks
    await asyncio.sleep_ms(500)
    try:
        await bootstrap_wifi(config)
    except Exception as e:
        print("Wi-Fi background init failed: {}".format(e))

async def main():
    global app_instance
    config = ConfigManager()
    app_instance = App(config=config)
    asyncio.create_task(_wifi_bg(config))
    await app_instance.run()

try:  # MicroPython runs main.py directly on boot;
    print("Boot: starting main.py")
    show_boot_indicator()
    asyncio.run(main())
except KeyboardInterrupt:
    print("Stopped by User.")
except Exception as e:
    print("FATAL ERROR: {}".format(e))
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
finally:
    # Crucial for soft resets in Thonny (pressing STOP button)!
    # Signals the Core 1 while loop to break and exit cleanly
    if 'app_instance' in globals() and app_instance is not None:
        if hasattr(app_instance, '_shutdown_render'):
            app_instance._shutdown_render = True
        
        # We also need to release the lock in case Core 1 is waiting on it!
        if hasattr(app_instance, '_render_lock') and app_instance._render_lock.locked():
            app_instance._render_lock.release()
            
        print("Core 1 Thread halted.")
        
    asyncio.new_event_loop() # Reset
