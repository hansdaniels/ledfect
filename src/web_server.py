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
        body { font-family: sans-serif; padding: 20px; background: #222; color: #fff; }
        .card { background: #333; padding: 15px; margin-bottom: 10px; border-radius: 8px; }
        button { padding: 10px; width: 100%; margin: 5px 0; background: #007bff; color: white; border: none; border-radius: 4px; }
        input { width: 100%; padding: 5px; margin: 5px 0; }
    </style>
</head>
<body>
    <h1>LED Controller</h1>
    <div class="card">
        <h3>Current Effect</h3>
        <div id="status">Loading...</div>
    </div>
    <div class="card">
        <h3>Controls</h3>
        <button onclick="setEffect('SolidColor')">Solid Color</button>
        <button onclick="setEffect('LarsonScanner')">Larson Scanner</button>
        <button onclick="setEffect('Rainbow')">Rainbow</button>
        <button onclick="setEffect('WanderingSpots')">Wandering Spots</button>
        <button onclick="setEffect('LavaLamp')">Lava Lamp</button>
        <button onclick="setEffect('Sparkle')">Sparkle</button>
        <button onclick="setEffect('Pulse')">Pulse</button>
    </div>
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
