import math
import random
from .utils import lerp, clamp, remap, hsv_to_rgb, kelvin_to_rgb

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

    def add_instance(self):
        pass

    def remove_instance(self):
        pass

    def set_color(self, color):
        """Set the main color of the effect."""
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
            idx = i * 3
            buffer[idx] = int(r)
            buffer[idx+1] = int(g)
            buffer[idx+2] = int(b) 
            
    def set_color(self, color):
        self.params["kelvin"] = None
        self.params["color"] = color

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
        self.params = {} # base params not really used if they each have their own
        self.scanners = [{
            "pos": 0.0, 
            "direction": 1, 
            "color": color, 
            "tail_length": tail_length, 
            "speed": speed
        }]

    def add_instance(self):
        new_color = hsv_to_rgb(random.random(), 1.0, 255)
        new_tail = random.randint(5, 30)
        new_speed = random.uniform(0.02, 0.3) 
        self.scanners.append({
            "pos": random.uniform(0, self.num_leds-1), 
            "direction": random.choice([-1, 1]),
            "color": new_color,
            "tail_length": new_tail,
            "speed": new_speed
        })

    def remove_instance(self):
        if len(self.scanners) > 1:
            self.scanners.pop()

    def get_state(self):
        return {"scanners": self.scanners}

    def set_state(self, state):
        if "scanners" in state:
            self.scanners = state["scanners"]

    def update(self, time_ms):
        # State-based bounce logic for variable speed
        if not hasattr(self, 'last_time'):
            self.last_time = time_ms
            return

        if time_ms == self.last_time:
            return

        dt = (time_ms - self.last_time) / 1000.0
        self.last_time = time_ms
        
        limit_fp = (self.num_leds - 1) * 256
        
        for scanner in self.scanners:
            speed = scanner.get("speed", 0.2)
            step_fp = int(dt * speed * 100.0 * 256)
            
            # Using fixed-point positions internally to prevent drift and allocations where possible
            if "pos_fp" not in scanner:
                scanner["pos_fp"] = int(scanner["pos"] * 256)
                
            scanner["pos_fp"] += step_fp * scanner["direction"]
            
            if scanner["pos_fp"] >= limit_fp:
                scanner["pos_fp"] = limit_fp
                scanner["direction"] = -1
            elif scanner["pos_fp"] <= 0:
                scanner["pos_fp"] = 0
                scanner["direction"] = 1
                
            scanner["pos"] = scanner["pos_fp"] / 256.0 # Kept for get_state backward compatibility

    def render(self, buffer):
        num_leds = self.num_leds
        for scanner in self.scanners:
            r, g, b = scanner["color"]
            tail_fp = int(scanner["tail_length"] * 256)
            center_fp = scanner.get("pos_fp", int(scanner["pos"] * 256))
            
            # Padding for start/end
            start = ((center_fp - tail_fp) >> 8) - 2
            if start < 0: start = 0
            end = ((center_fp + tail_fp) >> 8) + 3
            if end > num_leds: end = num_leds
            
            if tail_fp <= 0: continue
            
            for i in range(start, end):
                # Fixed-point distance to avoid float allocation (which triggers GC and causes stuttering)
                dist_fp = center_fp - (i << 8)
                if dist_fp < 0: dist_fp = -dist_fp
                
                if dist_fp < 256:
                    factor = 256
                elif dist_fp < tail_fp:
                    factor = 256 - ((dist_fp << 8) // tail_fp)
                else:
                    continue
                
                idx = i * 3
                nr = buffer[idx] + ((r * factor) >> 8)
                buffer[idx] = nr if nr <= 255 else 255
                
                ng = buffer[idx+1] + ((g * factor) >> 8)
                buffer[idx+1] = ng if ng <= 255 else 255
                
                nb = buffer[idx+2] + ((b * factor) >> 8)
                buffer[idx+2] = nb if nb <= 255 else 255

    def set_color(self, color):
        if self.scanners:
            self.scanners[-1]["color"] = color

    def randomize(self):
        # Randomize all scanners
        for scanner in self.scanners:
            scanner["color"] = hsv_to_rgb(random.random(), 1.0, 255)
            scanner["speed"] = random.uniform(0.2, 1.5)
            scanner["tail_length"] = random.randint(5, 30)

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
        if not hasattr(self, 'prev_time') or self.prev_time == 0:
            self.prev_time = time_ms
            return
            
        if time_ms == self.prev_time:
            return

        time_delta = time_ms - self.prev_time
        self.prev_time = time_ms
        for spot in self.spots:
            spot.update(time_delta)

    def render(self, buffer):
        num_leds = self.num_leds
        for spot in self.spots:
            width_fp = int(spot.width * 256)
            pos_fp = int(spot.pos * 256)
            
            # The original code only rendered up to spot.width despite start/end having * 2
            start = ((pos_fp - width_fp) >> 8) - 1
            if start < 0: start = 0
            end = ((pos_fp + width_fp) >> 8) + 2
            if end > num_leds: end = num_leds
            
            if width_fp <= 0: continue
            
            sr, sg, sb = spot.color
            
            for i in range(start, end):
                dist_fp = pos_fp - (i << 8)
                if dist_fp < 0: dist_fp = -dist_fp
                
                if dist_fp >= width_fp: continue
                
                # Integer approximation of math.exp bell curve: (1 - (dist/width)^2)^2
                x_fp = (dist_fp << 8) // width_fp
                xsq_fp = (x_fp * x_fp) >> 8
                inv_xsq_fp = 256 - xsq_fp
                factor = (inv_xsq_fp * inv_xsq_fp) >> 8
                
                if factor <= 0: continue

                idx = i * 3
                nr = buffer[idx] + ((sr * factor) >> 8)
                buffer[idx] = nr if nr <= 255 else 255
                
                ng = buffer[idx+1] + ((sg * factor) >> 8)
                buffer[idx+1] = ng if ng <= 255 else 255
                
                nb = buffer[idx+2] + ((sb * factor) >> 8)
                buffer[idx+2] = nb if nb <= 255 else 255

    def set_color(self, color):
        if self.spots:
            self.spots[-1].color = color

    def randomize(self):
        # Reset spots with new params
        count = random.randint(2, 6)
        self.spots = [self.Spot(self.num_leds) for _ in range(count)]

    def add_instance(self):
        self.spots.append(self.Spot(self.num_leds))

    def remove_instance(self):
        if len(self.spots) > 1:
            self.spots.pop()

    def get_state(self):
        return {
            "num_spots": len(self.spots),
            "color": self.spots[-1].color if self.spots else (255, 255, 255)
        }

    def set_state(self, state):
        if "num_spots" in state:
            num = state["num_spots"]
            color = state.get("color", (255, 255, 255))
            self.spots = [self.Spot(self.num_leds) for _ in range(num)]
            for s in self.spots:
                s.color = color

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
        if not hasattr(self, '_last_time'):
            self._last_time = time_ms
            return
            
        if time_ms == self._last_time:
            return
            
        self._last_time = time_ms
        
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
                idx = i * 3
                buffer[idx] = (r * bright) >> 8
                buffer[idx+1] = (g * bright) >> 8
                buffer[idx+2] = (b * bright) >> 8

    def set_color(self, color):
        self.params["color"] = color

    def randomize(self):
        self.params["speed"] = random.randint(5, 20)
        self.params["density"] = random.randint(1, 10)
        if random.random() > 0.5:
             self.params["color"] = None # Random colors
        else:
             self.params["color"] = hsv_to_rgb(random.random(), 1.0, 255)

    def add_instance(self):
        self.params["density"] = min(100, self.params["density"] + 5)

    def remove_instance(self):
        self.params["density"] = max(1, self.params["density"] - 5)

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
        
        # Precompute integer steps for fixed-point math to avoid per-pixel float allocations
        step_fp = int(scale * lut_len * 256)
        start_fp = int(self.offset * lut_len * 256)
        
        for i in range(self.num_leds):
            # Fixed point math: avoids float allocation (which triggers GC)
            val_fp = start_fp + i * step_fp
            idx = (val_fp >> 8) % lut_len
            
            r, g, b = lut[idx]
            
            idx_buf = i * 3
            buffer[idx_buf] = r
            buffer[idx_buf+1] = g
            buffer[idx_buf+2] = b

    def set_color(self, color):
        pass # Rainbow ignores solid colors

    def randomize(self):
        self.params["speed"] = random.randint(1, 20)
        self.params["scale"] = random.uniform(0.05, 0.5)

    def add_instance(self):
        self.params["scale"] = max(0.01, self.params["scale"] - 0.05)

    def remove_instance(self):
        self.params["scale"] = min(2.0, self.params["scale"] + 0.05)

class PulseEffect(BaseEffect):
    def __init__(self, num_leds, color=(0, 0, 255), speed=1.0):
        super().__init__(num_leds)
        self.params = {
            "color": color,
            "speed": speed
        }
    
    def update(self, time_ms):
        self._current_time = time_ms / 1000.0

    def render(self, buffer):
        t = getattr(self, '_current_time', 0.0) * self.params["speed"]
        brightness = (math.sin(t) + 1) / 2
        r = int(self.params["color"][0] * brightness)
        g = int(self.params["color"][1] * brightness)
        b = int(self.params["color"][2] * brightness)
        for i in range(self.num_leds):
            idx = i * 3
            buffer[idx] = r
            buffer[idx+1] = g
            buffer[idx+2] = b

    def set_color(self, color):
        self.params["color"] = color

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

    def __init__(self, num_leds, base_color=(10, 0, 30), blob_color=(255, 60, 0), num_blobs=3):
        super().__init__(num_leds)
        self.params = {
            "base_color": base_color,
            "blob_color": blob_color,
        }
        self.blobs = [self.Blob(num_leds, blob_color) for _ in range(num_blobs)]

    def _derive_base_color(self, blob_color):
        r, g, b = blob_color
        cr = 255 - r
        cg = 255 - g
        cb = 255 - b
        return (
            min(255, int(r * 0.05 + cr * 0.12)),
            min(255, int(g * 0.05 + cg * 0.12)),
            min(255, int(b * 0.05 + cb * 0.12)),
        )

    def update(self, time_ms):
        if not hasattr(self, '_last_time'):
            self._last_time = time_ms
            return
            
        if time_ms == self._last_time:
            return
            
        self._last_time = time_ms
        for blob in self.blobs:
            blob.update()

    def render(self, buffer):
        br, bg, bb = self.params["base_color"]
        for i in range(self.num_leds):
            idx = i * 3
            buffer[idx] = br
            buffer[idx+1] = bg
            buffer[idx+2] = bb

        for blob in self.blobs:
            size_fp = int(blob.size * 256)
            pos_fp = int(blob.pos * 256)
            radius_fp = size_fp * 2 # original code rendered up to 2 * size
            
            start = ((pos_fp - radius_fp) >> 8) - 1
            if start < 0: start = 0
            end = ((pos_fp + radius_fp) >> 8) + 2
            if end > self.num_leds: end = self.num_leds
            
            if radius_fp <= 0: continue
            
            br, bg, bb = blob.color
            
            for i in range(start, end):
                dist_fp = pos_fp - (i << 8)
                if dist_fp < 0: dist_fp = -dist_fp
                
                if dist_fp >= radius_fp: continue
                
                # Integer approximation of LavaLamp exponential falloff...
                # Using (1 - (dist/radius)^2)^4 for a tighter bell matching original exp falloff
                x_fp = (dist_fp << 8) // radius_fp
                xsq_fp = (x_fp * x_fp) >> 8
                inv_xsq_fp = 256 - xsq_fp
                f2 = (inv_xsq_fp * inv_xsq_fp) >> 8
                factor = (f2 * f2) >> 8
                
                if factor < 13: continue # 13/256 roughly equals 0.05 cutoff from original
                
                idx = i * 3
                nr = buffer[idx] + ((br * factor) >> 8)
                buffer[idx] = nr if nr <= 255 else 255
                
                ng = buffer[idx+1] + ((bg * factor) >> 8)
                buffer[idx+1] = ng if ng <= 255 else 255
                
                nb = buffer[idx+2] + ((bb * factor) >> 8)
                buffer[idx+2] = nb if nb <= 255 else 255

    def set_color(self, color):
        r, g, b = color
        r_f, g_f, b_f = r / 255.0, g / 255.0, b / 255.0
        cmax = max(r_f, g_f, b_f)
        cmin = min(r_f, g_f, b_f)
        delta = cmax - cmin
        
        if delta == 0:
            h = 0
        elif cmax == r_f:
            h = ((g_f - b_f) / delta) % 6
        elif cmax == g_f:
            h = ((b_f - r_f) / delta) + 2
        else:
            h = ((r_f - g_f) / delta) + 4
            
        h = h / 6.0
        
        base_r, base_g, base_b = hsv_to_rgb(h, 1.0, 30)
        self.params["base_color"] = (base_r, base_g, base_b)
        
        comp_h = (h + 0.5) % 1.0
        comp_r, comp_g, comp_b = hsv_to_rgb(comp_h, 1.0, 255)
            
        for blob in self.blobs:
            blob.color = (comp_r, comp_g, comp_b)

    def randomize(self):
        blob_color = hsv_to_rgb(random.random(), 1.0, 255)
        self.set_color(blob_color)
        num_blobs = random.randint(2, 5)
        self.blobs = [self.Blob(self.num_leds, blob_color) for _ in range(num_blobs)]

    def add_instance(self):
        self.blobs.append(self.Blob(self.num_leds, self.params.get("blob_color", (255, 60, 0))))

    def remove_instance(self):
        if len(self.blobs) > 1:
            self.blobs.pop()

    def get_state(self):
        state = self.params.copy()
        state["num_blobs"] = len(self.blobs)
        return state

    def set_state(self, state):
        super().set_state(state)
        if "num_blobs" in state:
            color = self.params.get("blob_color", (255, 60, 0))
            self.blobs = [self.Blob(self.num_leds, color) for _ in range(state["num_blobs"])]


class FadingSparkleEffect(BaseEffect):
    def __init__(self, num_leds, color=None, max_brightness=255, num_fading=3, fade_duration=2000):
        super().__init__(num_leds)
        self.params = {
            "color": color,            # None for random
            "max_brightness": max_brightness,
            "num_fading": num_fading,  # Max number of LEDs to fade in/out per cycle (1-3)
            "fade_duration": fade_duration # ms
        }
        self.leds = {} # index -> { "brightness": float, "color": tuple, "dir": 1/-1 }
        self.last_time = 0
        self.cycle_phase = "INIT" # INIT, FADING
        
    def update(self, time_ms):
        if not hasattr(self, 'last_time') or self.last_time == 0:
            self.last_time = time_ms
            # Start initial fade in
            self._start_cycle(initial=True)
            return

        if time_ms == self.last_time:
            return

        dt = time_ms - self.last_time
        self.last_time = time_ms
        
        # Update brightness
        finished_count = 0
        active_count = 0
        
        MAX_B = self.params["max_brightness"]
        DURATION = self.params["fade_duration"]
        step = (MAX_B / DURATION) * dt

        to_remove = []

        for idx, state in self.leds.items():
            active_count += 1
            if state["dir"] > 0: # Fading In
                state["brightness"] += step
                if state["brightness"] >= MAX_B:
                    state["brightness"] = MAX_B
                    finished_count += 1
            elif state["dir"] < 0: # Fading Out
                state["brightness"] -= step
                if state["brightness"] <= 0:
                    state["brightness"] = 0
                    to_remove.append(idx)
        
        # Clean up fully faded out LEDs
        for idx in to_remove:
            del self.leds[idx]
        
        fading_in_active = False
        for state in self.leds.values():
            if state["dir"] > 0 and state["brightness"] < MAX_B:
                fading_in_active = True
                break
        
        if not fading_in_active:
            self._start_cycle(initial=False)

    def _start_cycle(self, initial=False):
        num_fading = self.params["num_fading"]
        MAX_B = self.params["max_brightness"]
        
        if not initial:
            candidates = [idx for idx, s in self.leds.items() if s["dir"] > 0]
            count_out = random.randint(1, num_fading)
            count_out = min(count_out, len(candidates))
            
            chosen_out = []
            temp_candidates = list(candidates)
            for _ in range(count_out):
                if not temp_candidates: break
                idx = random.randint(0, len(temp_candidates) - 1)
                chosen_out.append(temp_candidates.pop(idx))
            for idx in chosen_out:
                self.leds[idx]["dir"] = -1 # Start fading out

        occupied = set(self.leds.keys())
        
        count_in = num_fading if initial else random.randint(1, num_fading)
        if initial: count_in = random.randint(5, 10) # "Starts with several"
        
        attempts = 0
        added = 0
        while added < count_in and attempts < 100:
            attempts += 1
            idx = random.randint(0, self.num_leds - 1)
            if idx not in occupied:
                # Pick color
                if self.params["color"]:
                    c = self.params["color"]
                else:
                    c = hsv_to_rgb(random.random(), 1.0, 255)
                
                self.leds[idx] = {
                    "brightness": 0.0,
                    "color": c,
                    "dir": 1 # Fade In
                }
                occupied.add(idx)
                added += 1

    def render(self, buffer):
        for idx, state in self.leds.items():
            b_val = state["brightness"]
            if b_val <= 0: continue
            
            r, g, b = state["color"]
            
            scale = b_val / 255.0
            
            idx_buf = idx * 3
            buffer[idx_buf] = int(r * scale)
            buffer[idx_buf+1] = int(g * scale)
            buffer[idx_buf+2] = int(b * scale)
            
    def set_color(self, color):
        self.params["color"] = color
        
    def randomize(self):
        if random.random() > 0.5:
             self.params["color"] = None
        else:
             self.params["color"] = hsv_to_rgb(random.random(), 1.0, 255)
             
        self.params["num_fading"] = random.randint(1, 5)
        self.params["fade_duration"] = random.randint(1000, 3000)

    def add_instance(self):
        self.params["num_fading"] = min(50, self.params["num_fading"] + 1)

    def remove_instance(self):
        self.params["num_fading"] = max(1, self.params["num_fading"] - 1)
