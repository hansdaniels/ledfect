import uasyncio as asyncio
import time
import math
import random
from machine import Pin
from neopixel import NeoPixel

# --- Konfiguration ---
NUM_LEDS = 300
np = NeoPixel(Pin(22), NUM_LEDS)


# --- Basisklasse für alle Effekte ---
class EffectLayer:
    def __init__(self, num_leds):
        self.num_leds = num_leds
        # Jeder Effekt bekommt seinen eigenen privaten Zeichenblock
        self.buffer = bytearray(num_leds * 3)
        self.active = True
        self.blend_mode = "ADD"  # 'ADD' (Licht mischen) oder 'OVERWRITE' (Übermalen)
        self.opacity = 1.0  # Globale Helligkeit des Layers (0.0 bis 1.0)

    async def update(self, dt):
        """Muss vom spezifischen Effekt überschrieben werden"""
        pass

    def clear(self):
        """Löscht den internen Buffer (Schwarz)"""
        # Slice-Zuweisung ist extrem schnell in MicroPython
        n = len(self.buffer)
        if n > 0:
            # Ein kleiner Trick um ein Bytearray schnell zu nullen ohne Loop
            # (Erstellt kurz ein temporäres Objekt, ist aber ok)
            self.buffer[:] = bytes(n)


# --- Der Manager (Compositor) ---
class Compositor:
    def __init__(self, neopixel_obj):
        self.np = neopixel_obj
        self.layers = []
        self.final_buffer = bytearray(len(neopixel_obj) * 3)  # Ausgabepuffer

    def add_layer(self, layer):
        self.layers.append(layer)

    def render(self):
        # 1. Finalen Buffer leeren
        self.final_buffer[:] = bytes(len(self.final_buffer))

        # 2. Alle aktiven Layer mischen
        # Um Performance zu sparen, nutzen wir lokale Variablen
        fb = self.final_buffer
        n = len(fb)

        for layer in self.layers:
            if not layer.active:
                continue

            lb = layer.buffer
            mode = layer.blend_mode

            # --- DER MISCH-LOOP (Performance kritisch) ---
            # Einfaches Additives Mischen ist sehr schnell.
            if mode == "ADD":
                for i in range(n):
                    # Nur rechnen, wenn im Layer was drin steht (Optimierung)
                    if lb[i] > 0:
                        val = fb[i] + lb[i]
                        if val > 255:
                            val = 255  # Clamping
                        fb[i] = val

            elif mode == "OVERWRITE":
                # Einfach drüberkopieren (sehr schnell)
                # Wir kopieren nur da, wo der Layer NICHT schwarz ist (Transparenz bei Schwarz)
                for i in range(n):
                    if lb[i] > 0:
                        fb[i] = lb[i]

        # 3. Auf Hardware schreiben
        for i in range(NUM_LEDS):
            idx = i * 3
            self.np[i] = (fb[idx], fb[idx + 1], fb[idx + 2])
        self.np.write()


# --- Effekt 1: Der Scanner (wie gehabt) ---
class ScannerLayer(EffectLayer):
    def __init__(self, num_leds, color, velocity, tail=5):
        super().__init__(num_leds)
        self.color = color
        self.velocity = velocity
        self.tail = tail
        self.pos = 0.0
        self.blend_mode = "ADD"  # Licht soll sich addieren

    async def update(self, dt):
        # 1. Buffer leeren (wichtig, sonst ziehen wir Spuren)
        self.clear()

        # 2. Position
        self.pos += self.velocity * dt
        if self.pos >= self.num_leds - 1:
            self.pos = self.num_leds - 1
            self.velocity = -self.velocity
        elif self.pos <= 0:
            self.pos = 0
            self.velocity = -self.velocity

        # 3. Zeichnen (in self.buffer!)
        start = int(self.pos - self.tail)
        end = int(self.pos + self.tail + 2)
        start = max(0, start)
        end = min(self.num_leds, end)

        r, g, b = self.color

        for i in range(start, end):
            dist = abs(i - self.pos)
            if dist < self.tail:
                factor = (1.0 - (dist / self.tail)) ** 2
                idx = i * 3
                # Wir schreiben direkt, kein += nötig, da Buffer leer war
                self.buffer[idx] = int(r * factor)
                self.buffer[idx + 1] = int(g * factor)
                self.buffer[idx + 2] = int(b * factor)


# --- Effekt 2: Langsamer Regenbogen Hintergrund ---
class RainbowLayer(EffectLayer):
    def __init__(self, num_leds):
        super().__init__(num_leds)
        self.offset = 0.0
        self.blend_mode = "ADD"  # Da der Scanner additiv ist, ist Hintergrund egal.
        # Wäre Scanner 'OVERWRITE', würde er den Hintergrund verdecken.

    async def update(self, dt):
        # Hier nutzen wir Chunking, um nicht zu blockieren
        self.offset += 0.5 * dt  # Langsame Bewegung

        chunk_size = 50
        for i in range(0, self.num_leds, chunk_size):
            end = min(i + chunk_size, self.num_leds)

            for j in range(i, end):
                hue = (j * 0.05) + self.offset
                # Dunkler Hintergrund (Max 30 Helligkeit)
                val = 20
                r = int((math.sin(hue) + 1) * val)
                g = int((math.sin(hue + 2) + 1) * val)
                b = int((math.sin(hue + 4) + 1) * val)

                idx = j * 3
                self.buffer[idx] = r
                self.buffer[idx + 1] = g
                self.buffer[idx + 2] = b

            # Task Yielding: Mitten im Update kurz atmen lassen
            await asyncio.sleep_ms(0)


# --- Effekt 3: Zufälliges Aufblitzen (Sparkle) ---
class SparkleLayer(EffectLayer):
    def __init__(self, num_leds):
        super().__init__(num_leds)
        self.next_sparkle = 0

    async def update(self, dt):
        # Fade out Effekt: Alles wird pro Frame etwas dunkler
        # Das erzeugt einen Nachleucht-Effekt
        for i in range(len(self.buffer)):
            if self.buffer[i] > 5:
                self.buffer[i] -= 5  # Dimmen
            else:
                self.buffer[i] = 0

        # Neuer Funke?
        now = time.ticks_ms()
        if now > self.next_sparkle:
            pos = random.randint(0, self.num_leds - 1)
            idx = pos * 3
            # Weißer Blitz
            self.buffer[idx] = 255
            self.buffer[idx + 1] = 255
            self.buffer[idx + 2] = 255
            self.next_sparkle = now + random.randint(50, 200)


class WanderingSpotsLayer(EffectLayer):

    # Eine kleine innere Klasse, um die Daten eines einzelnen Spots zu halten
    class _Spot:
        def __init__(self, color, speed, radius, wait_time_ms):
            self.color = color
            self.speed_magnitude = speed  # Pixel pro Sekunde (immer positiv)
            self.radius = radius
            self.wait_time_ms = wait_time_ms

            # Initialer Zustand
            self.pos = float(random.randint(0, NUM_LEDS - 1))
            self.target_pos = self.pos
            self.state = "WAITING"  # Startet wartend, sucht sich dann ein Ziel
            self.arrival_timestamp = time.ticks_ms()
            self.current_velocity = 0.0

    def __init__(self, num_leds, num_spots=5):
        super().__init__(num_leds)
        self.blend_mode = "ADD"
        self.spots = []

        # Wir erstellen mehrere Spots mit zufälligen Eigenschaften
        for _ in range(num_spots):
            g = random.randint(0, 200)
            r = random.randint(0, 200)
            b = random.randint(0, 200)
            color = (g, r, b)

            # Zufallsgeschwindigkeit (z.B. zwischen 20 und 100 Pixel/sek)
            speed = random.uniform(3.0, 15.0)

            # Zufallsradius (Wie breit ist der Spot? z.B. 3 bis 8 LEDs)
            radius = random.uniform(3.0, 15.0)

            # Zufallswartezeit (z.B. 0.5 bis 3 Sekunden)
            wait_ms = random.randint(500, 3000)

            self.spots.append(self._Spot(color, speed, radius, wait_ms))

    def _pick_new_target(self, spot):
        """Hilfsfunktion: Wählt ein neues Ziel für einen Spot aus."""
        # Neues Zufallsziel im gültigen Bereich
        spot.target_pos = float(random.randint(0, self.num_leds - 1))

        # Richtung bestimmen
        if spot.target_pos > spot.pos:
            direction = 1.0
        else:
            direction = -1.0

        # Geschwindigkeit setzen (Richtung * Betrag)
        spot.current_velocity = direction * spot.speed_magnitude
        spot.state = "MOVING"

    async def update(self, dt):
        # 1. Buffer leeren für das neue Frame
        self.clear()
        now = time.ticks_ms()

        # 2. Logik für jeden Spot aktualisieren
        for spot in self.spots:

            # --- STATE MACHINE ---
            if spot.state == "MOVING":
                # Wie weit bewegen wir uns in diesem Frame?
                step = spot.current_velocity * dt

                # Abstand zum Ziel vor der Bewegung
                dist_to_target = spot.target_pos - spot.pos

                # Prüfen, ob wir im nächsten Schritt über das Ziel hinausschießen würden
                # (Wenn der Schritt größer ist als der Restabstand)
                if abs(step) >= abs(dist_to_target):
                    # Wir sind angekommen!
                    spot.pos = spot.target_pos
                    spot.state = "WAITING"
                    spot.arrival_timestamp = now
                else:
                    # Normal weiterbewegen
                    spot.pos += step

            elif spot.state == "WAITING":
                # Prüfen, ob die Wartezeit vorbei ist
                if time.ticks_diff(now, spot.arrival_timestamp) > spot.wait_time_ms:
                    # Neues Ziel suchen und losreisen
                    self._pick_new_target(spot)

            # --- RENDERING (Zeichnen) ---
            # Zeichne den Spot an seiner aktuellen Position in den Buffer
            self._draw_spot_to_buffer(spot)

    def _draw_spot_to_buffer(self, spot):
        """Zeichnet einen einzelnen Spot mit weichem Abfall."""
        # Bereich berechnen, den der Spot betrifft
        start_idx = int(spot.pos - spot.radius)
        end_idx = int(spot.pos + spot.radius + 1)

        # Clamping auf Strip-Grenzen
        start_idx = max(0, start_idx)
        end_idx = min(self.num_leds, end_idx)

        r_base, g_base, b_base = spot.color

        for i in range(start_idx, end_idx):
            # Abstand zur exakten Float-Mitte
            dist = abs(i - spot.pos)

            if dist < spot.radius:
                # Quadratische Abnahme für weiches Licht (Zentrum=1.0, Rand=0.0)
                factor = (1.0 - (dist / spot.radius)) ** 2

                idx = i * 3
                # Additives Zeichnen in den eigenen Buffer
                self.buffer[idx] += int(r_base * factor)
                self.buffer[idx + 1] += int(g_base * factor)


# --- Hauptprogramm ---
async def main():
    comp = Compositor(np)

    comp.add_layer(RainbowLayer(NUM_LEDS))
    #comp.add_layer(ScannerLayer(NUM_LEDS, (255, 0, 0), 20.0, 8))
    #comp.add_layer(ScannerLayer(NUM_LEDS, (0, 0, 255), 30.0, 11))
    #comp.add_layer(ScannerLayer(NUM_LEDS, (0, 200, 0), 10.0, 6))
    comp.add_layer(SparkleLayer(NUM_LEDS))
    #comp.add_layer(WanderingSpotsLayer(NUM_LEDS, num_spots=7))
    last_time = time.ticks_ms()

    while True:
        now = time.ticks_ms()
        dt = time.ticks_diff(now, last_time) / 1000.0
        last_time = now

        # 1. Alle Effekte updaten lassen
        # Jeder Effekt darf so lange rechnen wie er will (oder yielden)
        for layer in comp.layers:
            if layer.active:
                await layer.update(dt)

        # 2. Mischen und Anzeigen
        # Das passiert zentral an einer Stelle
        comp.render()

        await asyncio.sleep_ms(10)


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
