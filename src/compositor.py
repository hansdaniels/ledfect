import array
from src.utils import blend_add, blend_alpha

BLEND_MODE_NORMAL = 0
BLEND_MODE_ADD = 1
BLEND_MODE_OVERLAY = 2  # Not yet implemented, placeholder

class Layer:
    def __init__(self, effect, blend_mode=BLEND_MODE_NORMAL, opacity=255):
        self.effect = effect
        self.blend_mode = blend_mode
        self.opacity = opacity  # 0-255 (Global opacity for this layer)
        self.active = True

class Compositor:
    def __init__(self, num_leds, debug=False):
        self.num_leds = num_leds
        self.layers = []
        # Main buffer: G, R, B, W (W unused for WS2815 usually, but structure keeps alignment)
        # Using bytearray for mutable buffer
        # WS2815 is usually GRB. We will store as RGB internally for easier math and convert if needed, 
        # or store GRB. Let's stick to RGB internally and map to hardware order in the strip driver.
        # Format: R, G, B per pixel.
        self.buffer = bytearray(num_leds * 3) 
        # Temp buffer for layers to render into before blending
        self.layer_buffer = bytearray(num_leds * 4) # R, G, B, A per pixel
        self.debug = debug

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
        # Clear main buffer (Black)
        for i in range(len(self.buffer)):
            self.buffer[i] = 0

        for layer in self.layers:
            if not layer.active or layer.opacity == 0:
                continue

            # Clear layer buffer
            # Optimization: Can move this into effect render if effect overwrites everything
            # But for safety, clear it.
            for i in range(len(self.layer_buffer)):
                self.layer_buffer[i] = 0

            # Render effect into layer_buffer (RGBA)
            layer.effect.render(self.layer_buffer)

            # Blend layer_buffer into self.buffer
            self._blend_layer(layer)
        
        return self.buffer

    def _blend_layer(self, layer):
        # This is the heavy loop, might need optimization in Viper/Asm later
        for i in range(self.num_leds):
            base_idx = i * 3
            layer_idx = i * 4
            
            base_r = self.buffer[base_idx]
            base_g = self.buffer[base_idx + 1]
            base_b = self.buffer[base_idx + 2]
            
            src_r = self.layer_buffer[layer_idx]
            src_g = self.layer_buffer[layer_idx + 1]
            src_b = self.layer_buffer[layer_idx + 2]
            src_a = self.layer_buffer[layer_idx + 3]
            
            # Combine pixel alpha with layer global opacity
            final_alpha = (src_a * layer.opacity) // 255
            
            if final_alpha == 0:
                continue

            if layer.blend_mode == BLEND_MODE_ADD:
                # Additive blending
                # Treat alpha as weight/intensity
                r, g, b = blend_add(base_r, base_g, base_b, src_r, src_g, src_b, final_alpha / 255.0)
                self.buffer[base_idx] = r
                self.buffer[base_idx + 1] = g
                self.buffer[base_idx + 2] = b
                
            elif layer.blend_mode == BLEND_MODE_NORMAL:
                # Standard Alpha Blending
                r, g, b = blend_alpha(base_r, base_g, base_b, src_r, src_g, src_b, final_alpha)
                self.buffer[base_idx] = r
                self.buffer[base_idx + 1] = g
                self.buffer[base_idx + 2] = b
                
            # TODO: Other blend modes
