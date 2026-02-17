import math
import random
from src.utils import lerp, clamp, remap, hsv_to_rgb, kelvin_to_rgb

class BaseEffect:
    def __init__(self, num_leds):
        self.num_leds = num_leds
        self.active = True
        self.params = {}

    def update(self, time_ms):
        pass

    def render(self, buffer):
        pass
    
    def get_state(self):
        return self.params

    def set_state(self, state):
        for k, v in state.items():
            if k in self.params:
                self.params[k] = v

    def randomize(self):
        """Randomize parameters for variety."""
        pass

class SolidColorEffect(BaseEffect):
    def __init__(self, num_leds, color=(255, 255, 255), kelvin=None):
        super().__init__(num_leds)
        self.params = {
            "color": color,
            "kelvin": kelvin 
        }
    
    def update(self, time_ms):
        if self.params["kelvin"] is not None:
            self.params["color"] = kelvin_to_rgb(self.params["kelvin"])

    def render(self, buffer):
        r, g, b = self.params["color"]
        for i in range(self.num_leds):
            idx = i * 4
            buffer[idx] = int(r)
            buffer[idx+1] = int(g)
            buffer[idx+2] = int(b)
            buffer[idx+3] = 255 
            
    def randomize(self):
        # Random hue or temperature
        if random.random() > 0.5:
             # Random Color
             self.params["kelvin"] = None
             self.params["color"] = hsv_to_rgb(random.random(), 1.0, 255)
        else:
            # Random Temperature
            k = random.randint(2000, 8000)
            self.params["kelvin"] = k

class LarsonScannerEffect(BaseEffect):
    def __init__(self, num_leds, color=(255, 0, 0), tail_length=10, speed=0.2):
        super().__init__(num_leds)
        self.params = {
            "color": color,
            "tail_length": tail_length,
            "speed": speed, 
            "width": 2.0 
        }
        self.pos = 0.0
        self.direction = 1

    def update(self, time_ms):
        # State-based bounce logic for variable speed
        if not hasattr(self, 'last_time'):
            self.last_time = time_ms
            return

        dt = (time_ms - self.last_time) / 1000.0
        self.last_time = time_ms
        
        speed = self.params.get("speed", 0.2)
        # speed is roughly "loops per second" or similar?
        # Let's say speed 1.0 = 50 pixels / sec
        
        step = dt * (speed * 100.0)
        
        self.pos += step * self.direction
        
        limit = self.num_leds - 1
        if self.pos >= limit:
            self.pos = limit
            self.direction = -1
        elif self.pos <= 0:
            self.pos = 0
            self.direction = 1

    def render(self, buffer):
        r, g, b = self.params["color"]
        center = self.pos
        tail = self.params["tail_length"]
        
        for i in range(self.num_leds):
            dist = abs(i - center)
            brightness = 0
            if dist < 1.0:
                brightness = 255 
            elif dist < tail:
                brightness = int(255 * (1 - (dist / tail)))
            else:
                brightness = 0
            
            if brightness > 0:
                idx = i * 4
                buffer[idx] = r
                buffer[idx+1] = g
                buffer[idx+2] = b
                buffer[idx+3] = brightness

    def randomize(self):
        self.params["color"] = hsv_to_rgb(random.random(), 1.0, 255)
        self.params["speed"] = random.uniform(0.2, 1.5)
        self.params["tail_length"] = random.randint(5, 30)

class WanderingSpotsEffect(BaseEffect):
    class Spot:
        def __init__(self, num_leds):
            self.pos = random.uniform(0, num_leds-1)
            self.target = random.uniform(0, num_leds-1)
            self.speed = random.uniform(0.05, 0.2)
            self.width = random.uniform(2, 5)
            self.color = hsv_to_rgb(random.random(), 1.0, 255)
            self.pause_frames = 0
            self.max_leds = num_leds

        def update(self, time_delta):
            if self.pause_frames > 0:
                self.pause_frames -= 1
                return

            dist = self.target - self.pos
            if abs(dist) < 0.5:
                self.pause_frames = random.randint(30, 100)
                self.target = random.uniform(0, self.max_leds-1)
                return

            step = self.speed * (time_delta / 16.0)
            if abs(dist) < step:
                self.pos = self.target
            else:
                self.pos += step if dist > 0 else -step

    def __init__(self, num_leds, num_spots=3):
        super().__init__(num_leds)
        self.spots = [self.Spot(num_leds) for _ in range(num_spots)]
        self.prev_time = 0

    def update(self, time_ms):
        time_delta = time_ms - self.prev_time if self.prev_time > 0 else 16
        self.prev_time = time_ms
        for spot in self.spots:
            spot.update(time_delta)

    def render(self, buffer):
        for spot in self.spots:
            start = int(spot.pos - spot.width * 2)
            end = int(spot.pos + spot.width * 2)
            start = max(0, start)
            end = min(self.num_leds, end)
            sr, sg, sb = spot.color
            
            for i in range(start, end):
                dist = abs(i - spot.pos)
                if dist > spot.width: continue
                factor = math.exp(-(dist*dist) / (2 * (spot.width/2)**2))
                brightness = int(255 * factor)
                if brightness <= 0: continue

                idx = i * 4
                cr = buffer[idx]
                cg = buffer[idx+1]
                cb = buffer[idx+2]
                ca = buffer[idx+3]
                new_a = max(ca, brightness)
                nr = cr + int(sr * factor)
                ng = cg + int(sg * factor)
                nb = cb + int(sb * factor)
                
                buffer[idx] = min(255, nr)
                buffer[idx+1] = min(255, ng)
                buffer[idx+2] = min(255, nb)
                buffer[idx+3] = min(255, new_a)

    def randomize(self):
        # Reset spots with new params
        count = random.randint(2, 6)
        self.spots = [self.Spot(self.num_leds) for _ in range(count)]

class SparkleEffect(BaseEffect):
    def __init__(self, num_leds, color=None, speed=10, density=5):
        super().__init__(num_leds)
        self.params = {
            "color": color, 
            "speed": speed, 
            "density": density 
        }
        self.pixels = [0] * num_leds 
        self.pixel_colors = [(0,0,0)] * num_leds

    def update(self, time_ms):
        decay = self.params["speed"]
        for i in range(self.num_leds):
            if self.pixels[i] > 0:
                self.pixels[i] = max(0, self.pixels[i] - decay)
        
        if random.randint(0, 100) < self.params["density"]:
            idx = random.randint(0, self.num_leds - 1)
            if self.pixels[idx] == 0:
                self.pixels[idx] = 255
                if self.params["color"]:
                    self.pixel_colors[idx] = self.params["color"]
                else:
                    self.pixel_colors[idx] = hsv_to_rgb(random.random(), 0.5, 255)

    def render(self, buffer):
        for i in range(self.num_leds):
            bright = self.pixels[i]
            if bright > 0:
                r, g, b = self.pixel_colors[i]
                idx = i * 4
                buffer[idx] = r
                buffer[idx+1] = g
                buffer[idx+2] = b
                buffer[idx+3] = bright

    def randomize(self):
        self.params["speed"] = random.randint(5, 20)
        self.params["density"] = random.randint(1, 10)
        if random.random() > 0.5:
             self.params["color"] = None # Random colors
        else:
             self.params["color"] = hsv_to_rgb(random.random(), 1.0, 255)

from .oklch import oklch_to_rgb

class RainbowEffect(BaseEffect):
    _RAINBOW_LUT = None

    def __init__(self, num_leds, speed=10, scale=0.5):
        super().__init__(num_leds)
        self.params = {
            "speed": speed, # 1-100 range roughly
            "scale": scale # Lower scale = longer waves
        }
        self.offset = 0
        
        if RainbowEffect._RAINBOW_LUT is None:
            RainbowEffect._precompute_rainbow()

    @classmethod
    def _precompute_rainbow(cls):
        print("--- RECOMPUTING RAINBOW LUT (V12: Sticky + Less Yellow/More Violet) ---")
        cls._RAINBOW_LUT = []
        
        # Manually tuned RGB anchors (V12)
        # Goal: Less Yellow/Orange, More Violet
        anchors = [
            (0.00, (255, 0, 0)),    # Red
            (0.25, (255, 127, 0)),  # Orange (25% Red->Orange)
            (0.35, (255, 255, 0)),  # Yellow (10% Orange->Yellow) -> Compressed
            (0.45, (0, 255, 0)),    # Green  (10% Yellow->Green) -> Compressed
            (0.60, (0, 255, 255)),  # Cyan   (15%)
            (0.70, (0, 0, 255)),    # Blue   (10%)
            (0.85, (128, 0, 255)),  # Purple (15% Blue->Purple) -> Expanded
            (1.00, (255, 0, 0)),    # Back to Red (15% Purple->Red) -> Expanded Violet
        ]
        
        steps = 360
        for i in range(steps):
            t = i / steps
            
            # Find segment
            for j in range(len(anchors) - 1):
                start_t, start_c = anchors[j]
                end_t, end_c = anchors[j+1]
                
                if t >= start_t and t <= end_t:
                    seg_len = end_t - start_t
                    if seg_len == 0: 
                        local_t = 0
                    else:
                        local_t = (t - start_t) / seg_len
                    
                    # V11 Adjustment: "Sticky" key colors
                    # Hold color for first 15% and last 15% of the segment.
                    # Blend only in the middle 70%.
                    # Remap 0.15..0.85 -> 0..1
                    sticky_t = (local_t - 0.15) / 0.7
                    sticky_t = max(0.0, min(1.0, sticky_t)) # Clamp
                    
                    # Use Smoothstep for nicer blend in the middle
                    blend_t = sticky_t * sticky_t * (3 - 2 * sticky_t)
                    
                    r = int(start_c[0] + (end_c[0] - start_c[0]) * blend_t)
                    g = int(start_c[1] + (end_c[1] - start_c[1]) * blend_t)
                    b = int(start_c[2] + (end_c[2] - start_c[2]) * blend_t)
                    
                    cls._RAINBOW_LUT.append((r, g, b))
                    break
            else:
                # Fallback
                cls._RAINBOW_LUT.append(anchors[-1][1])

    def update(self, time_ms):
        # Time based animation for smoothness
        # speed 10 => 0.1 cycle/sec => 10s period
        t = time_ms / 1000.0
        self.offset = t * (self.params["speed"] * 0.005)

    def render(self, buffer):
        scale = self.params["scale"] * 0.1
        lut = self._RAINBOW_LUT
        lut_len = len(lut)
        
        for i in range(self.num_leds):
            # hue calculated as 0.0-1.0 float in classic logic
            # Map that to 0..359 index
            val = (i * scale + self.offset) % 1.0
            idx = int(val * lut_len) % lut_len
            
            r, g, b = lut[idx]
            
            idx_buf = i * 4
            buffer[idx_buf] = r
            buffer[idx_buf+1] = g
            buffer[idx_buf+2] = b
            buffer[idx_buf+3] = 255

    def randomize(self):
        self.params["speed"] = random.randint(1, 20)
        self.params["scale"] = random.uniform(0.05, 0.5)

class PulseEffect(BaseEffect):
    def __init__(self, num_leds, color=(0, 0, 255), speed=1.0):
        super().__init__(num_leds)
        self.params = {
            "color": color,
            "speed": speed
        }
    
    def update(self, time_ms):
        pass 

    def render(self, buffer):
        import time
        t = time.ticks_ms() / 1000.0 * self.params["speed"]
        brightness = (math.sin(t) + 1) / 2
        r = int(self.params["color"][0] * brightness)
        g = int(self.params["color"][1] * brightness)
        b = int(self.params["color"][2] * brightness)
        for i in range(self.num_leds):
            idx = i * 4
            buffer[idx] = r
            buffer[idx+1] = g
            buffer[idx+2] = b
            buffer[idx+3] = 255

    def randomize(self):
        self.params["color"] = hsv_to_rgb(random.random(), 1.0, 255)
        self.params["speed"] = random.uniform(0.5, 3.0)

class LavaLampEffect(BaseEffect):
    class Blob:
        def __init__(self, num_leds, color):
            self.pos = random.uniform(0, num_leds)
            self.velocity = random.uniform(0.05, 0.2) * (1 if random.random() > 0.5 else -1)
            self.size = random.uniform(5, 15)
            self.color = color
            self.limit = num_leds

        def update(self):
            self.pos += self.velocity
            if self.pos > self.limit or self.pos < 0:
                self.velocity *= -1

    def __init__(self, num_leds, base_color=(10, 0, 30), blob_color=(255, 100, 0), num_blobs=3):
        super().__init__(num_leds)
        self.params = {
            "base_color": base_color
        }
        self.blobs = [self.Blob(num_leds, blob_color) for _ in range(num_blobs)]

    def update(self, time_ms):
        for blob in self.blobs:
            blob.update()

    def render(self, buffer):
        br, bg, bb = self.params["base_color"]
        for i in range(self.num_leds):
            idx = i * 4
            buffer[idx] = br
            buffer[idx+1] = bg
            buffer[idx+2] = bb
            buffer[idx+3] = 255

        for blob in self.blobs:
            start = int(blob.pos - blob.size * 2)
            end = int(blob.pos + blob.size * 2)
            start = max(0, start)
            end = min(self.num_leds, end)
            for i in range(start, end):
                dist = abs(i - blob.pos)
                if dist > blob.size * 2: continue
                # Increased blob influence slightly for visibility
                val = math.exp(-(dist*dist)/(2*(blob.size/2.2)**2)) 
                if val < 0.05: continue
                
                idx = i * 4
                # Additive blending but clamped
                r = buffer[idx] + int(blob.color[0] * val)
                g = buffer[idx+1] + int(blob.color[1] * val)
                b = buffer[idx+2] + int(blob.color[2] * val)
                buffer[idx] = min(255, r)
                buffer[idx+1] = min(255, g)
                buffer[idx+2] = min(255, b)

    def randomize(self):
        # Generate complementary or triadic colors for contrast
        hue_blob = random.random()
        hue_base = (hue_blob + 0.5 + random.uniform(-0.1, 0.1)) % 1.0
        
        blob_color = hsv_to_rgb(hue_blob, 1.0, 255)
        # Darker base color to make blobs pop
        base_color = hsv_to_rgb(hue_base, 1.0, 50) 
        
        self.params["base_color"] = base_color
        num_blobs = random.randint(2, 5)
        self.blobs = [self.Blob(self.num_leds, blob_color) for _ in range(num_blobs)]
