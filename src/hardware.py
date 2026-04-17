import machine
import neopixel
import uasyncio as asyncio
import time

class StripController:
    def __init__(self, pin_num, num_leds):
        self.num_leds = num_leds
        self.pin = machine.Pin(pin_num, machine.Pin.OUT)
        self.np = neopixel.NeoPixel(self.pin, num_leds)
        
    def write(self, buffer):
        # buffer is bytearray of size num_leds * 3 (RGB)
        # NeoPixel expects (r, g, b)
        n = self.num_leds
        for i in range(n):
            idx = i * 3
            # WS2812/WS2815 are usually GRB.
            # Convert RGB buffer -> GRB for the strip
            self.np[i] = (buffer[idx+1], buffer[idx], buffer[idx+2])
        self.np.write()

class Button:
    def __init__(self, pin_num, name, long_press_ms=1000):
        self.pin = machine.Pin(pin_num, machine.Pin.IN, machine.Pin.PULL_UP)
        self.name = name
        self.long_press_ms = long_press_ms
        self._last_state = 1
        self._press_time = 0
        self._long_triggered = False

    def check(self):
        """
        Returns:
        0: No event
        1: Short Press (on release)
        2: Long Press (triggered after time)
        """
        val = self.pin.value() # 0 = Pressed
        now = time.ticks_ms()
        event = 0
        
        if val == 0 and self._last_state == 1:
            # Pressed just now
            self._press_time = now
            self._long_triggered = False
        
        elif val == 0 and self._last_state == 0:
            # Holding
            if not self._long_triggered:
                if time.ticks_diff(now, self._press_time) > self.long_press_ms:
                    self._long_triggered = True
                    event = 2 # Long Press detected
        
        elif val == 1 and self._last_state == 0:
            # Released
            if not self._long_triggered:
                # Was a short press
                # Debounce check (e.g. > 50ms)
                if time.ticks_diff(now, self._press_time) > 50:
                    event = 1 # Short Press
            
        self._last_state = val
        return event

class Potentiometer:
    def __init__(self, pin_num):
        self.adc = machine.ADC(pin_num)
        self.value = 0.0
        self._history = [0] * 5
        self._ptr = 0
    
    def read(self):
        raw = self.adc.read_u16()
        self._history[self._ptr] = raw
        self._ptr = (self._ptr + 1) % len(self._history)
        avg = sum(self._history) / len(self._history)
        self.value = avg / 65535.0
        return self.value

class PIRSensor:
    def __init__(self, pin_num):
        self.pin = machine.Pin(pin_num, machine.Pin.IN, machine.Pin.PULL_DOWN)
        self.triggered = False
        self.pin.irq(trigger=machine.Pin.IRQ_RISING, handler=self._handler)
        self._last_trigger = 0

    def _handler(self, pin):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_trigger) > 2000: 
            self.triggered = True
            self._last_trigger = now

    def check(self):
        now = time.ticks_ms()
        if self.triggered:
            self.triggered = False
            return True
        if self.pin.value() and time.ticks_diff(now, self._last_trigger) > 2000:
            self._last_trigger = now
            return True
        return False

class IRReceiver:
    def __init__(self, pin_num):
        from .ir_rx import NEC_IR
        self.last_code = None
        self.last_addr = None
        self.active_code = None
        self.repeat_count = 0
        self.long_press_triggered = False
        self.ir = NEC_IR(machine.Pin(pin_num, machine.Pin.IN), self._callback)
        
    def _callback(self, data, addr, ctrl):
        if data < 0:
            if self.active_code is not None and not self.long_press_triggered:
                self.repeat_count += 1
                if self.repeat_count >= 6: # Reduced threshold (roughly 600-700ms hold for NEC)
                    if self.last_code is None: # Guarantee we NEVER overwrite an unread short press
                        self.long_press_triggered = True
                        self.last_code = self.active_code + 0x1000
            return
            
        self.last_code = data
        self.last_addr = addr
        self.active_code = data
        self.repeat_count = 0
        self.long_press_triggered = False

    def get_code(self):
        if self.last_code is not None:
            c = self.last_code
            self.last_code = None
            return c
        return None

    def get_debug_pulses(self):
        return self.ir.get_debug_pulses()

class LightSensor:
    def __init__(self, pin_num):
        self.pin = machine.Pin(pin_num, machine.Pin.IN)
    
    def read(self):
        return self.pin.value()


class Buzzer:
    def __init__(self, pin_num=None):
        self.pin = None
        self._beeping = False
        if pin_num is not None:
            self.pin = machine.Pin(pin_num, machine.Pin.OUT)
            self.pin.value(0)

    def is_enabled(self):
        return self.pin is not None

    def on(self):
        if self.pin is not None:
            self.pin.value(1)

    def off(self):
        if self.pin is not None:
            self.pin.value(0)

    async def beep(self, duration_ms=50):
        if self.pin is None or self._beeping:
            return
        self._beeping = True
        try:
            self.on()
            await asyncio.sleep_ms(duration_ms)
            self.off()
        finally:
            self._beeping = False
