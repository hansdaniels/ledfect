import array
import micropython
# from src.utils import blend_add, blend_alpha # Inlined now

BLEND_MODE_NORMAL = 0
BLEND_MODE_ADD = 1
BLEND_MODE_OVERLAY = 2

class Layer:
    def __init__(self, effect, blend_mode=BLEND_MODE_NORMAL, opacity=255):
        self.effect = effect
        self.blend_mode = blend_mode
        self.opacity = opacity
        self.active = True

class Compositor:
    def __init__(self, num_leds, debug=False):
        self.num_leds = num_leds
        self.layers = []
        self.buffer = bytearray(num_leds * 3) 
        self.layer_buffer = bytearray(num_leds * 4)
        self.debug = debug
        self._blank_buffer = bytearray(num_leds * 3)
        self._blank_layer = bytearray(num_leds * 4)

    def add_layer(self, layer):
        self.layers.append(layer)

    def remove_layer(self, layer):
        if layer in self.layers:
            self.layers.remove(layer)

    def clear_layers(self):
        self.layers = []

    def update(self, time_ms):
        for layer in self.layers:
            if layer.active:
                layer.effect.update(time_ms)

    def render(self):
        # Clear main buffer (Black) using fast slice assignment
        self.buffer[:] = self._blank_buffer

        for layer in self.layers:
            if not layer.active or layer.opacity == 0:
                continue

            # Clear layer buffer
            self.layer_buffer[:] = self._blank_layer

            # Render effect into layer_buffer (RGBA)
            layer.effect.render(self.layer_buffer)

            # Blend layer_buffer into self.buffer
            self._blend_layer(layer)
        
        return self.buffer

    @micropython.native
    def _blend_layer(self, layer):
        num_leds = self.num_leds
        buf = self.buffer
        l_buf = self.layer_buffer
        mode = int(layer.blend_mode)
        opacity = int(layer.opacity)
        
        # Pre-calc to avoid object lookup in loop
        # But we need arrays. 
        # Access to bytearray in native is fast.
        
        for i in range(num_leds):
            base_idx = i * 3
            layer_idx = i * 4
            
            # src RGBA
            src_r = l_buf[layer_idx]
            src_g = l_buf[layer_idx + 1]
            src_b = l_buf[layer_idx + 2]
            src_a = l_buf[layer_idx + 3]
            
            # Global opacity blend
            # Fast approx: (src_a * opacity) >> 8
            final_alpha = (src_a * opacity) >> 8
            
            if final_alpha == 0:
                continue
                
            base_r = buf[base_idx]
            base_g = buf[base_idx + 1]
            base_b = buf[base_idx + 2]
            
            if mode == 1: # BLEND_MODE_ADD
                # Additive
                # r = base + (src * alpha)
                r = base_r + ((src_r * final_alpha) >> 8)
                g = base_g + ((src_g * final_alpha) >> 8)
                b = base_b + ((src_b * final_alpha) >> 8)
                
                # Clamp
                if r > 255: r = 255
                if g > 255: g = 255
                if b > 255: b = 255
                
                buf[base_idx] = r
                buf[base_idx + 1] = g
                buf[base_idx + 2] = b
                
            elif mode == 0: # BLEND_MODE_NORMAL
                # Alpha Blend
                # out = (src * alpha + base * (256 - alpha)) >> 8
                inv_alpha = 256 - final_alpha
                
                r = (src_r * final_alpha + base_r * inv_alpha) >> 8
                g = (src_g * final_alpha + base_g * inv_alpha) >> 8
                b = (src_b * final_alpha + base_b * inv_alpha) >> 8
                
                buf[base_idx] = r
                buf[base_idx + 1] = g
                buf[base_idx + 2] = b
