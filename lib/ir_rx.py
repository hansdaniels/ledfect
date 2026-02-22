import time
import rp2
from machine import Pin
import uasyncio as asyncio

# PIO program for NEC IR decoding
# Wait for 9ms low (mark), 4.5ms high (space), then decode 32 bits based on space duration.
# PIO program for NEC IR decoding
# At 1MHz, 1 cycle = 1us.

class NEC_IR:
    def __init__(self, pin, callback):
        self.pin = pin
        self.callback = callback
        
        # Start PIO StateMachine
        # Clock at 1MHz so 1 cycle = 1us.
        self.sm = rp2.StateMachine(0, nec_decoder, freq=1000000, in_base=self.pin, jmp_pin=self.pin)
        self.sm.active(1)
        
        # We need a CPU thread to poll the PIO FIFO
        # The PIO pushes a full 32-bit word after it receives it.
        # FIFO depth is 4 words, so we can survive up to 4 button presses between polls!
        asyncio.create_task(self._poll_loop())

    async def _poll_loop(self):
        while True:
            # DRAIN the FIFO completely!
            # If we only read 1 per 50ms, the PIO FIFO (4 words) overflows and freezes the hardware decoder mid-bit!
            while self.sm.rx_fifo() > 0:
                data = self.sm.get()
                
                if data == 0xFFFFFFFF:
                    self.callback(-1, -1, 0)
                else:
                    addr     = (data >> 24) & 0xFF
                    addr_inv = (data >> 16) & 0xFF
                    cmd      = (data >> 8)  & 0xFF
                    cmd_inv  = (data >> 0)  & 0xFF
                    
                    # Log to confirm
                    print(f"IR Decoding -> Addr: 0x{addr:02X}, Cmd: 0x{cmd:02X} (Raw: 0x{data:08X})")
                    
                    # STRICT Validate: Only accept it if the mathematical NEC checksum passes.
                    # This guarantees we NEVER push garbled noise!
                    if (cmd ^ cmd_inv) == 0xFF and (addr ^ addr_inv) == 0xFF:
                        self.callback(cmd, addr, 0)
                    # We silently discard corrupted checksums (often caused by letting go of a button)
                
            await asyncio.sleep_ms(50)

    def get_debug_pulses(self):
        return None

@rp2.asm_pio(set_init=rp2.PIO.IN_HIGH, autopush=True, push_thresh=32)
def nec_decoder():
    # Frequency: 1MHz (1us per cycle)
    wrap_target()
    
    label("idle")
    wait(0, pin, 0)
    
    # Measure 9ms Low (Start Mark)
    set(x, 30)
    label("mark_loop")
    jmp(pin, "idle")        # Pin went HIGH early -> Noise
    set(y, 29)
    label("delay")
    jmp(y_dec, "delay") [8] # 10us loop
    jmp(x_dec, "mark_loop")
    
    # Start Mark valid. Wait for it to finish.
    wait(1, pin, 0)
    
    # Measure Start Space
    # We need to distinguish between 4.5ms (Data) and 2.25ms (Repeat).
    # We will count down from 31 with a ~100us delay.
    # 31 * 100us = 3.1ms threshold.
    set(x, 31)
    label("start_space")
    jmp(pin, "space_high")
    
    # Pin went LOW. Check X to see if it took > 3.1ms
    # If X > 0, we finished EARLY (Time < 3.1ms) -> REPEAT CODE!
    # If X == 0, we finished LATE (Time > 3.1ms) -> DATA CODE!
    # X counts down to 0, so if we're here, we test X.
    # The simplest way: if we branch past the X decrements, we have 4.5ms.
    
    label("space_high")
    set(y, 9)
    label("delay_space")
    jmp(y_dec, "delay_space") [8] # 10us loop
    jmp(x_dec, "start_space")
    
    # Start Space > 3.1ms long. It's a Data Code!
    # Wait for Start Space to finish
    wait(0, pin, 0)
    
    # We will read exactly 32 bits
    set(y, 31)
    
    label("bit_loop")
    # Wait for the Data Mark to finish (560us low)
    wait(1, pin, 0)
    
    # Measure Data Space
    # Logic 0 space is ~560us. Logic 1 space is ~1690us.
    # Threshold ~1000us.
    set(x, 31)
    
    label("measure_bit")
    jmp(pin, "bit_high")
    
    # Pin went LOW -> Space ended.
    # If X is STILL NOT 0, the space was SHORT (<1000us) -> Logic 0.
    set(x, 0)
    in_(x, 1)
    jmp(y_dec, "bit_loop")
    jmp("idle")             # 32 bits read, autopush handles it
    
    label("bit_high")
    jmp(x_dec, "measure_bit") [30] # 32us loop = 992us max
    
    # If X hits 0, the space was LONG (>1000us) -> Logic 1.
    set(x, 1)
    in_(x, 1)
    wait(0, pin, 0)         # Wait for space to finish
    jmp(y_dec, "bit_loop")
    wrap()
