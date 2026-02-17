import math

def clamp(val, min_val, max_val):
    return max(min_val, min(val, max_val))

def lerp(start, end, t):
    return start + (end - start) * t

def remap(val, in_min, in_max, out_min, out_max):
    return (val - in_min) * (out_max - out_min) / (in_max - in_min) + out_min

def hsv_to_rgb(h, s, v):
    """
    Convert HSV to RGB.
    h: 0.0 - 1.0
    s: 0.0 - 1.0
    v: 0.0 - 255.0 (for consistency with 8-bit color)
    Returns (r, g, b) tuple.
    """
    if s == 0.0:
        v = int(v)
        return (v, v, v)
    
    i = int(h * 6.0)
    f = (h * 6.0) - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    
    i = i % 6
    
    if i == 0: return (int(v), int(t), int(p))
    if i == 1: return (int(q), int(v), int(p))
    if i == 2: return (int(p), int(v), int(t))
    if i == 3: return (int(p), int(q), int(v))
    if i == 4: return (int(t), int(p), int(v))
    if i == 5: return (int(v), int(p), int(q))
    return (0, 0, 0)

def kelvin_to_rgb(temp):
    """
    Approximate RGB from Kelvin temperature.
    """
    temp = clamp(temp, 1000, 40000) / 100.0
    
    # Red
    if temp <= 66:
        r = 255
    else:
        r = temp - 60
        r = 329.698727446 * (r ** -0.1332047592)
        r = clamp(r, 0, 255)
        
    # Green
    if temp <= 66:
        g = temp
        g = 99.4708025861 * (math.log(g) if g > 0 else 0) - 161.1195681661
        g = clamp(g, 0, 255)
    else:
        g = temp - 60
        g = 288.1221695283 * (g ** -0.0755148492)
        g = clamp(g, 0, 255)
        
    # Blue
    if temp >= 66:
        b = 255
    else:
        if temp <= 19:
            b = 0
        else:
            b = temp - 10
            b = 138.5177312231 * (math.log(b) if b > 0 else 0) - 305.0447927307
            b = clamp(b, 0, 255)
            
    return (int(r), int(g), int(b))


def blend_add(base_r, base_g, base_b, add_r, add_g, add_b, alpha_factor=1.0):
    """
    Additive blending with proportional scaling if max > 255.
    alpha_factor: 0.0 - 1.0, scales the added color before addition.
    """
    r = base_r + (add_r * alpha_factor)
    g = base_g + (add_g * alpha_factor)
    b = base_b + (add_b * alpha_factor)
    
    max_val = max(r, g, b)
    if max_val > 255:
        scale = 255.0 / max_val
        r *= scale
        g *= scale
        b *= scale
        
    return int(r), int(g), int(b)

def blend_alpha(base_r, base_g, base_b, top_r, top_g, top_b, alpha):
    """
    Standard alpha blending.
    alpha: 0 (base fully visible) to 255 (top fully visible)
    """
    inv_alpha = 255 - alpha
    r = (top_r * alpha + base_r * inv_alpha) // 255
    g = (top_g * alpha + base_g * inv_alpha) // 255
    b = (top_b * alpha + base_b * inv_alpha) // 255
    return r, g, b

def scale_buffer(buffer, scale):
    """
    Scale all values in buffer by scale (0.0 - 1.0).
    Modifies buffer in-place.
    """
    if scale >= 1.0: return
    
    # Integer math approximation for speed: scale * 256
    # fp_scale = int(scale * 256)
    # Using float is okay in MicroPython logic usually, but int is faster.
    # Buffer is bytearray.
    
    for i in range(len(buffer)):
        buffer[i] = int(buffer[i] * scale)

