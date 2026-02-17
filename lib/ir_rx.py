import time
from machine import Pin
# Minimal NEC decoder
class NEC_IR:
    def __init__(self, pin, callback):
        self.pin = pin
        self.callback = callback
        self.pin.irq(handler=self._cb, trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING)
        self.t_last = time.ticks_us()
        self.bits = 0
        self.cmd = 0
        self.state = 0 # 0=Idle, 1=Start Mark, 2=Start Space, 3=Data
        
    def _cb(self, pin):
        t = time.ticks_us()
        dt = time.ticks_diff(t, self.t_last)
        self.t_last = t
        
        # Simple Logic:
        # 9ms Mark -> 4.5ms Space -> Data
        # We just track duration between edges.
        # But for robustness, we use a library pattern usually.
        # Given limitations, I'll use a very simplified pulse-distance decoder.
        # Rising edge to Rising edge distance:
        # 13.5ms = Start
        # 2.25ms = '1'
        # 1.125ms = '0'
        # Repetition: 11.25ms
        
        # Since interrupt triggers on both, we can just look at long spaces?
        # Let's trigger on FALLING edge only to measure period from previous falling?
        # No, NEC encodes in pulse position.
        
        if dt > 8000: # Start or long gap
            self.bits = 0
            self.cmd = 0
            return
            
        # If we assume we are getting valid pulse trains:
        # 2250us = 1, 1120us = 0 (approx)
        if dt > 1500: # Logic 1
            self.cmd = (self.cmd << 1) | 1
            self.bits += 1
        elif dt > 700: # Logic 0
            self.cmd = (self.cmd << 1)
            self.bits += 1
            
        if self.bits == 32:
            # Address (8), ~Address (8), Command (8), ~Command (8)
            # Just return the whole 32 bits or parse
            # cmd is usually bits 16-23
            # But order depends on LSB/MSB first.
            # Usually LSB first. But this shift logic is MSB first.
            # We'll just return raw for mapping.
            self.callback(self.cmd & 0xFF, (self.cmd >> 8) & 0xFF, 0)
            self.bits = 0
            self.cmd = 0
