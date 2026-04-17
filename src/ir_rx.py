import time
import rp2
from machine import Pin
import uasyncio as asyncio


class NEC_IR:
    def __init__(self, pin, callback):
        self.pin = pin
        self.callback = callback
        self.sm = rp2.StateMachine(0, nec_decoder, freq=1000000, in_base=self.pin, jmp_pin=self.pin)
        self.sm.active(1)
        asyncio.create_task(self._poll_loop())

    async def _poll_loop(self):
        while True:
            while self.sm.rx_fifo() > 0:
                data = self.sm.get()

                if data == 0xFFFFFFFF:
                    self.callback(-1, -1, 0)
                else:
                    addr = (data >> 24) & 0xFF
                    addr_inv = (data >> 16) & 0xFF
                    cmd = (data >> 8) & 0xFF
                    cmd_inv = (data >> 0) & 0xFF

                    print(f"IR Decoding -> Addr: 0x{addr:02X}, Cmd: 0x{cmd:02X} (Raw: 0x{data:08X})")

                    if (cmd ^ cmd_inv) == 0xFF and (addr ^ addr_inv) == 0xFF:
                        self.callback(cmd, addr, 0)

            await asyncio.sleep_ms(50)

    def get_debug_pulses(self):
        return None


@rp2.asm_pio(set_init=rp2.PIO.IN_HIGH, autopush=True, push_thresh=32)
def nec_decoder():
    wrap_target()

    label("idle")
    wait(0, pin, 0)

    set(x, 30)
    label("mark_loop")
    jmp(pin, "idle")
    set(y, 29)
    label("delay")
    jmp(y_dec, "delay") [8]
    jmp(x_dec, "mark_loop")

    wait(1, pin, 0)

    set(x, 31)
    label("start_space")
    jmp(pin, "space_high")

    mov(isr, invert(null))
    push()
    jmp("idle")

    label("space_high")
    set(y, 9)
    label("delay_space")
    jmp(y_dec, "delay_space") [8]
    jmp(x_dec, "start_space")

    wait(0, pin, 0)

    set(y, 31)

    label("bit_loop")
    wait(1, pin, 0)

    set(x, 31)

    label("measure_bit")
    jmp(pin, "bit_high")

    nop() [31]
    jmp(pin, "bit_high") [31]

    in_(null, 1)
    jmp(y_dec, "bit_loop")
    jmp("idle")

    label("bit_high")
    nop() [6]
    jmp(x_dec, "measure_bit") [30]

    in_(x, 1)
    wait(0, pin, 0)
    jmp(y_dec, "bit_loop")
    wrap()
