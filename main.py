import uasyncio as asyncio
import time
import _thread
from src.config import ConfigManager
from src.hardware import StripController, Button, Potentiometer, PIRSensor, IRReceiver, LightSensor
from src.effects import SolidColorEffect, LarsonScannerEffect, WanderingSpotsEffect, SparkleEffect, RainbowEffect, PulseEffect, LavaLampEffect, FadingSparkleEffect
from src.web_server import WebServer
from src.utils import scale_buffer
import gc

# Pin Definitions
PIN_LEDS = 22
PIN_BTN_R = 9
PIN_BTN_C = 12
PIN_BTN_L = 10
PIN_POT = 27
PIN_PIR = 15
PIN_IR = 14
PIN_LIGHT = 21

NUM_LEDS = 300

class App:
    def __init__(self):
        self.config = ConfigManager()
        
        # Hardware
        self.strip = StripController(PIN_LEDS, NUM_LEDS)
        self.btn_r = Button(PIN_BTN_R, "Next")
        self.btn_c = Button(PIN_BTN_C, "Mode")
        self.btn_l = Button(PIN_BTN_L, "Prev")
        self.pot = Potentiometer(PIN_POT)
        self.pir = PIRSensor(PIN_PIR)
        self.ir = IRReceiver(PIN_IR)
        self.light = LightSensor(PIN_LIGHT)
        
        # Logic
        self.buffer = bytearray(NUM_LEDS * 3)
        self.blank_buffer = bytearray(NUM_LEDS * 3)
        self.current_effect = None
        self.current_effect_name = self.config.get("effect", "SolidColor")
        self.brightness = self.config.get("brightness", 255)
        self.pir_enabled = self.config.get("pir_enabled", True)
        self.last_motion_time = time.time()
        self.is_off_due_to_timeout = False
        self.is_off_manual = False
        self.manual_off_brightness_cache = 255
        
        # Load initial effect
        self._load_effect(self.current_effect_name)
        
        # Web Server
        self.server = WebServer(self)
        
        # Second Core Render Thread
        self._render_buffer = bytearray(NUM_LEDS * 3)
        self._render_ready = False
        self._shutdown_render = False
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

    def _load_effect(self, name):
        self.current_effect_name = name
        
        effect = None
        if name == "SolidColor":
            effect = SolidColorEffect(NUM_LEDS, color=(255, 100, 0))
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
            
        self.config.set("effect", name)

    def get_status(self):
        return {
            "effect": self.current_effect_name,
            "brightness": self.brightness,
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
            is_on = not self.is_off_due_to_timeout and not self.is_off_manual
            
            if is_on:
                if self.current_effect:
                    if not getattr(self, 'is_paused', False):
                        self.logical_time += dt_real
                        
                    self.current_effect.update(self.logical_time)
                    self.buffer[:] = self.blank_buffer
                    self.current_effect.render(self.buffer)
                
                # Apply Global Brightness (from Potentiometer usually, or Web)
                # Apply Potentiometer read to self.brightness?
                # Let's say Pot overrides or scales Web brightness?
                # Usually Pot is absolute if moved, or we just take Pot * MaxBrightness.
                # Let's just use Pot as master scaler [0.0 - 1.0] for the config brightness.
                master_scale = self.pot.read()
                
                # Special mapping for LarsonScanner: Pot = Speed
                if self.current_effect_name == "LarsonScanner":
                    if self.current_effect:
                        # Logarithmic-ish mapping for better feel? Linear is fine.
                        new_speed = 0.05 + (master_scale * 3.0) 
                        self.current_effect.params["speed"] = new_speed
                    
                    # Brightness controlled only by web/config (fixed scale 1.0 from pot perspective)
                    master_scale = 1.0 
                
                # We need to scale the buffer.
                final_scale = master_scale * (self.brightness / 255.0)
                
                if final_scale < 0.99:
                    scale_buffer(self.buffer, final_scale)
                
                # Check if the buffer actually changed before writing.
                if not hasattr(self, '_last_written_buffer') or self.buffer != self._last_written_buffer:
                    # Handoff to Second Core
                    # We only signal the thread if it's currently waiting (flag is False)
                    # If it's already busy writing the previous frame, we just skip (frame drop)
                    # to maintain timing!
                    if not self._render_ready:
                        self._render_buffer[:] = self.buffer
                        self._render_ready = True # Signals thread to go!
                    
                    self._last_written_buffer = bytearray(self.buffer)
                    
                self._is_black_written = False
            else:
                # Off state
                if getattr(self, '_is_black_written', False) == False:
                    if not self._render_ready:
                        self._render_buffer[:] = bytearray(NUM_LEDS * 3)
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
                print(f"Input Loop Alive (Pot: {self.pot.value:.2f})")
            
            # Buttons
            # Buttons
            if self.btn_r.check() == 1:
                self.reset_activity()
                print("Button Right Pressed - Switching Effect")
                # Next effect
                try:
                    idx = effects.index(self.current_effect_name)
                    next_idx = (idx + 1) % len(effects)
                    print(f"Switching to {effects[next_idx]}")
                    self._load_effect(effects[next_idx])
                except ValueError:
                    self._load_effect(effects[0])
            
            if self.btn_l.check() == 1:
                self.reset_activity()
                print("Button Left Pressed - Prev Effect")
                 # Prev effect
                try:
                    idx = effects.index(self.current_effect_name)
                    next_idx = (idx - 1) % len(effects)
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
            elif evt_c == 2:
                # Long Press: Toggle On/Off
                print("Button Center Long: Toggle On/Off")
                self.is_off_manual = not self.is_off_manual
                if self.is_off_manual:
                     print("Manual Off")
                else:
                     print("Manual On")

            # Potentiometer
            # Read in animation loop for speed? Or here?
            # Reading ADC is fast.
            self.pot.read() 
            
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

                if code in ir_mapping["STOP/MODE"]:
                    self.is_off_manual = not self.is_off_manual
                    if self.is_off_manual:
                         print("Manual Off (IR)")
                    else:
                         print("Manual On (IR)")
                         
                elif code in ir_mapping["RIGHT"]: # Right = Next
                    idx = effects.index(self.current_effect_name)
                    next_idx = (idx + 1) % len(effects)
                    self._load_effect(effects[next_idx])
                    
                elif code in ir_mapping["LEFT"]: # Left = Prev
                    idx = effects.index(self.current_effect_name)
                    next_idx = (idx - 1) % len(effects)
                    self._load_effect(effects[next_idx])
                    
                elif code in ir_mapping["PLAY/PAUSE"]:
                    self.is_paused = not getattr(self, 'is_paused', False)
                    print(f"Effect Paused: {self.is_paused}")
                        
                elif code in ir_mapping["VOL+"] or code in ir_mapping["UP"]:
                    self.brightness = min(255, self.brightness + 25)
                    self.config.set("brightness", self.brightness)
                    
                elif code in ir_mapping["VOL-"] or code in ir_mapping["DOWN"]:
                    self.brightness = max(0, self.brightness - 25)
                    self.config.set("brightness", self.brightness)

            await asyncio.sleep_ms(50)

    async def pir_loop(self):
        # Timeout: 10 mins = 600 * 1000 ms
        TIMEOUT_MS = 600 * 1000
        while True:
            if self.pir.check(): 
                # Motion detected
                self.reset_activity()
            
            # Check timeout
            if not self.is_off_due_to_timeout:
                if time.time() - self.last_motion_time > 600: # 600 seconds
                    print("No motion for 10m. Turning off.")
                    self.is_off_due_to_timeout = True
                    # Clear strip
                    self.strip.write(bytearray(NUM_LEDS * 3)) # Black
            
            await asyncio.sleep_ms(1000)

    async def light_sensor_loop(self):
        while True:
            val = self.light.read()
            print(f"Lichtsensor (GP{PIN_LIGHT}): {val}")
            await asyncio.sleep(2) # 2 seconds

app_instance = None

async def main():
    global app_instance
    app_instance = App()
    await app_instance.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped by User.")
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
