import uasyncio as asyncio
import ujson

class WebServer:
    def __init__(self, app_context):
        self.app = app_context # Reference to main App class to trigger actions

    async def handle_client(self, reader, writer):
        try:
            request_line = await reader.readline()
            if not request_line:
                writer.close()
                return

            method, path, _ = request_line.decode().split()
            
            # Read headers to find content length (basic)
            content_length = 0
            while True:
                line = await reader.readline()
                if not line or line == b'\r\n':
                    break
                if line.lower().startswith(b'content-length:'):
                    content_length = int(line.split(b':')[1].strip())

            # Handle requests
            response_body = ""
            status = "200 OK"
            content_type = "text/html"

            if method == "GET" and path == "/":
                response_body = self._get_html()
            
            elif method == "GET" and path == "/api/status":
                content_type = "application/json"
                response_body = ujson.dumps(self.app.get_status())
                
            elif method == "POST" and path == "/api/config":
                if content_length > 0:
                    body = await reader.read(content_length)
                    data = ujson.loads(body)
                    self.app.update_config(data)
                    content_type = "application/json"
                    response_body = '{"status": "ok"}'
                else:
                    status = "400 Bad Request"
                    response_body = "Missing body"
            else:
                status = "404 Not Found"
                response_body = "Not Found"

            response = f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {len(response_body)}\r\nConnection: close\r\n\r\n{response_body}"
            writer.write(response.encode())
            await writer.drain()
        except Exception as e:
            print(f"Web error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def start(self, port=80):
        print(f"Starting web server on port {port}")
        await asyncio.start_server(self.handle_client, "0.0.0.0", port)

    def _get_html(self):
        # Basic control interface
        return """<!DOCTYPE html>
<html>
<head>
    <title>Pico LED Control</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {
            --bg: #f4efe6;
            --card: #fffaf2;
            --ink: #223034;
            --muted: #607074;
            --accent: #1a8d70;
            --accent-2: #d96c2f;
            --line: #dccfbf;
        }
        body { font-family: Verdana, sans-serif; padding: 20px; background: linear-gradient(180deg, #fffdf8 0%, var(--bg) 100%); color: var(--ink); margin: 0; }
        main { max-width: 880px; margin: 0 auto; }
        .card { background: var(--card); padding: 16px; margin-bottom: 14px; border-radius: 14px; border: 2px solid var(--line); box-shadow: 0 10px 24px rgba(57, 43, 23, 0.08); }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
        button { padding: 12px; width: 100%; margin: 0; background: var(--accent); color: white; border: none; border-radius: 999px; font-weight: bold; }
        pre { white-space: pre-wrap; margin: 0; font-size: 0.95rem; }
        h1, h3 { margin-top: 0; }
        p { color: var(--muted); }
        table { width: 100%; border-collapse: collapse; font-size: 0.95rem; }
        th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--line); vertical-align: top; }
        th { color: var(--accent-2); }
        .key { font-weight: bold; white-space: nowrap; }
        .swatch {
            display: inline-block;
            width: 0.95rem;
            height: 0.95rem;
            border-radius: 999px;
            margin-right: 0.55rem;
            vertical-align: -0.1rem;
            border: 1px solid rgba(34, 48, 52, 0.2);
            box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.35);
        }
    </style>
</head>
<body>
    <main>
        <h1>LED Controller</h1>
        <div class="card">
            <h3>Current Status</h3>
            <pre id="status">Loading...</pre>
        </div>
        <div class="card">
            <h3>Effects</h3>
            <div class="grid">
                <button onclick="setEffect('SolidColor')">Solid Color</button>
                <button onclick="setEffect('LarsonScanner')">Larson Scanner</button>
                <button onclick="setEffect('Rainbow')">Rainbow</button>
                <button onclick="setEffect('WanderingSpots')">Wandering Spots</button>
                <button onclick="setEffect('LavaLamp')">Lava Lamp</button>
                <button onclick="setEffect('Sparkle')">Sparkle</button>
                <button onclick="setEffect('Pulse')">Pulse</button>
                <button onclick="setEffect('FadingSparkle')">Fading Sparkle</button>
            </div>
        </div>
        <div class="card">
            <h3>IR Remote Help</h3>
            <p>The page below mirrors the button handling currently implemented in the Pico code.</p>
            <table>
                <tr><th>Button</th><th>Action</th></tr>
                <tr><td class="key">1</td><td><span class="swatch" style="background:#ff0000;"></span>Red.</td></tr>
                <tr><td class="key">2</td><td><span class="swatch" style="background:#ff8000;"></span>Orange.</td></tr>
                <tr><td class="key">3</td><td><span class="swatch" style="background:#ffd400;"></span>Yellow.</td></tr>
                <tr><td class="key">4</td><td><span class="swatch" style="background:#00c853;"></span>Green.</td></tr>
                <tr><td class="key">5</td><td><span class="swatch" style="background:#00d5ff;"></span>Cyan.</td></tr>
                <tr><td class="key">6</td><td><span class="swatch" style="background:#0057ff;"></span>Blue.</td></tr>
                <tr><td class="key">7</td><td><span class="swatch" style="background:#ff00c8;"></span>Magenta.</td></tr>
                <tr><td class="key">8</td><td><span class="swatch" style="background:#8040ff;"></span>Violet.</td></tr>
                <tr><td class="key">9</td><td><span class="swatch" style="background:#ffffff;"></span>White.</td></tr>
                <tr><td class="key">STOP/MODE</td><td>Toggle LEDs on or off.</td></tr>
                <tr><td class="key">SETUP</td><td>Enable or disable PIR timeout.</td></tr>
                <tr><td class="key">ENTER/SAVE</td><td>Arm or disarm night mode.</td></tr>
                <tr><td class="key">UP</td><td>Next effect.</td></tr>
                <tr><td class="key">DOWN</td><td>Previous effect.</td></tr>
                <tr><td class="key">RIGHT</td><td>Increase animation speed.</td></tr>
                <tr><td class="key">LEFT</td><td>Decrease animation speed.</td></tr>
                <tr><td class="key">PLAY/PAUSE</td><td>Pause or resume the current effect.</td></tr>
                <tr><td class="key">VOL+</td><td>Increase brightness in fixed steps.</td></tr>
                <tr><td class="key">VOL-</td><td>Decrease brightness in fixed steps.</td></tr>
                <tr><td class="key">0/10+</td><td>Add an effect instance or increase a value for effects that support it.</td></tr>
                <tr><td class="key">BACK</td><td>Remove an effect instance or decrease a value for effects that support it.</td></tr>
            </table>
        </div>
    </main>
    <script>
        async function updateStatus() {
            try {
                let res = await fetch('/api/status');
                let data = await res.json();
                document.getElementById('status').innerText = JSON.stringify(data, null, 2);
            } catch(e) {}
        }
        async function setEffect(name) {
            await fetch('/api/config', {
                method: 'POST',
                body: JSON.stringify({effect: name})
            });
            setTimeout(updateStatus, 500);
        }
        setInterval(updateStatus, 2000);
        updateStatus();
    </script>
</body>
</html>"""
