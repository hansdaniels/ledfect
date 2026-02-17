import uasyncio as asyncio
import time
from src.config import ConfigManager
from src.compositor import Compositor, Layer, BLEND_MODE_ADD
from src.hardware import StripController, Button, Potentiometer, PIRSensor, IRReceiver
from src.effects import SolidColorEffect, LarsonScannerEffect, WanderingSpotsEffect, SparkleEffect, RainbowEffect, PulseEffect, LavaLampEffect
from src.web_server import WebServer
from src.utils import scale_buffer
import gc

# Pin Definitions
PIN_LEDS = 22
PIN_BTN_R = 9
PIN_BTN_C = 12
PIN_BTN_L = 10
PIN_POT = 27
PIN_PIR = 16
PIN_IR = 17

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
        
        # Logic
        self.compositor = Compositor(NUM_LEDS)
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

    def reset_activity(self):
        """Reset the user activity timer (wakes up if sleeping)."""
        self.last_motion_time = time.time()
        if self.is_off_due_to_timeout:
            print("Activity detected! Waking up.")
            self.is_off_due_to_timeout = False

    def _load_effect(self, name):
        self.compositor.clear_layers()
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
        else:
            effect = SolidColorEffect(NUM_LEDS, color=(50,50,50)) # Fallback
            
        if effect:
            # We can have multiple layers. For now one active effect layer.
            # Maybe a background layer?
            self.compositor.add_layer(Layer(effect))
            
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
        
        # Main Animation Loop
        print("Starting Animation Loop")
        while True:
            t0 = time.ticks_ms()
            
            # Logic Update (Only render if ON)
            is_on = not self.is_off_due_to_timeout and not self.is_off_manual
            
            if is_on:
                self.compositor.update(t0)
                
                # Render
                buffer = self.compositor.render()
                
                # Apply Global Brightness (from Potentiometer usually, or Web)
                # Apply Potentiometer read to self.brightness?
                # Let's say Pot overrides or scales Web brightness?
                # Usually Pot is absolute if moved, or we just take Pot * MaxBrightness.
                # Let's just use Pot as master scaler [0.0 - 1.0] for the config brightness.
                master_scale = self.pot.read()
                
                # Special mapping for LarsonScanner: Pot = Speed
                if self.current_effect_name == "LarsonScanner":
                    # Map 0.0-1.0 to Speed 0.05 - 3.0
                    if self.compositor.layers:
                        # Speed needs to be updated on the effect instance
                        eff = self.compositor.layers[0].effect
                        # Logarithmic-ish mapping for better feel? Linear is fine.
                        new_speed = 0.05 + (master_scale * 3.0) 
                        eff.params["speed"] = new_speed
                    
                    # Brightness controlled only by web/config (fixed scale 1.0 from pot perspective)
                    master_scale = 1.0 
                
                # We need to scale the buffer.
                # Note: modifying buffer in place is risky if compositor reuses it? 
                # Compositor returns mutable ref to self.buffer.
                # Use a simple check: if brightness < 1.0, scale.
                
                final_scale = master_scale * (self.brightness / 255.0)
                
                if final_scale < 0.99:
                    scale_buffer(buffer, final_scale)
                
                self.strip.write(buffer)
            else:
                # Off state
                # Write black once? We should ensure strip is cleared when entering off state.
                # Just writing black repeatedly is fine.
                self.strip.write(bytearray(NUM_LEDS * 3))

            # Frame pacing
            t1 = time.ticks_ms()
            diff = time.ticks_diff(t1, t0)
            wait = max(0, 16 - diff) # ~60fps cap
            await asyncio.sleep_ms(wait)

    async def input_loop(self):
        print("Input Loop Started")
        effects = ["SolidColor", "LarsonScanner", "WanderingSpots", "Sparkle", "Rainbow", "Pulse", "LavaLamp"]
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
                if self.compositor.layers:
                    self.compositor.layers[0].effect.randomize()
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
                self.reset_activity()
                # TODO: Map specific codes to actions
                # Just next effect for any code for now as test
                idx = effects.index(self.current_effect_name)
                next_idx = (idx + 1) % len(effects)
                self._load_effect(effects[next_idx])

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

async def main():
    app = App()
    await app.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped")
    finally:
        asyncio.new_event_loop() # Reset
