import machine
import network
import time
import uasyncio as asyncio


SETUP_AP_PREFIX = "PicoRoomSetup"
SETUP_AP_PASSWORD = "kidslights"
SETUP_AP_CHANNEL = 6
STA_CONNECT_TIMEOUT_MS = 15000
STA_POLL_INTERVAL_MS = 250


class WiFiManager:
    def __init__(self, config):
        self.config = config
        self.sta = network.WLAN(network.STA_IF)
        self.ap = network.WLAN(network.AP_IF)
        self.status_led = machine.Pin("LED", machine.Pin.OUT)
        self.status_led.value(0)
        self._portal_done = False
        self._portal_status = "Select a Wi-Fi network."

    async def flash_sos(self, repeats=2):
        unit_ms = 180
        pattern = (
            (1, 1), (0, 1), (1, 1), (0, 1), (1, 1), (0, 3),
            (1, 3), (0, 1), (1, 3), (0, 1), (1, 3), (0, 3),
            (1, 1), (0, 1), (1, 1), (0, 1), (1, 1),
        )
        for repeat in range(repeats):
            for level, units in pattern:
                self.status_led.value(level)
                await asyncio.sleep_ms(unit_ms * units)
            self.status_led.value(0)
            if repeat < repeats - 1:
                await asyncio.sleep_ms(unit_ms * 7)

    def has_saved_credentials(self):
        ssid = self.config.get("wifi_ssid", "")
        return bool(ssid)

    def connect_saved(self, timeout_ms=STA_CONNECT_TIMEOUT_MS):
        ssid = self.config.get("wifi_ssid", "")
        password = self.config.get("wifi_password", "")
        if not ssid:
            print("Wi-Fi: no saved credentials.")
            return False
        return self.connect(ssid, password, timeout_ms=timeout_ms)

    def connect(self, ssid, password="", timeout_ms=STA_CONNECT_TIMEOUT_MS):
        self.ap.active(False)
        self.sta.active(True)
        hostname = self.config.get("wifi_hostname", "pico-led")
        try:
            self.sta.config(hostname=hostname)
        except Exception:
            pass

        print("Wi-Fi: connecting to '{}'...".format(ssid))
        self.sta.disconnect()
        time.sleep_ms(200)
        self.sta.connect(ssid, password or "")

        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
            status = self.sta.status()
            if status == network.STAT_GOT_IP:
                ip = self.sta.ifconfig()[0]
                print("Wi-Fi connected: {}".format(ip))
                return True
            if status in (network.STAT_WRONG_PASSWORD, network.STAT_NO_AP_FOUND, network.STAT_CONNECT_FAIL):
                print("Wi-Fi failed with status {}".format(status))
                break
            time.sleep_ms(STA_POLL_INTERVAL_MS)

        self.sta.disconnect()
        print("Wi-Fi: connection timed out.")
        return False

    def _ap_ssid(self):
        suffix = "".join("{:02X}".format(b) for b in machine.unique_id()[-2:])
        return "{}-{}".format(SETUP_AP_PREFIX, suffix)

    def _url_decode(self, text):
        result = []
        i = 0
        length = len(text)
        while i < length:
            ch = text[i]
            if ch == "+":
                result.append(" ")
                i += 1
            elif ch == "%" and i + 2 < length:
                try:
                    result.append(chr(int(text[i + 1:i + 3], 16)))
                    i += 3
                except ValueError:
                    result.append(ch)
                    i += 1
            else:
                result.append(ch)
                i += 1
        return "".join(result)

    def _parse_form(self, body):
        data = {}
        if not body:
            return data
        for pair in body.split("&"):
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            data[self._url_decode(key)] = self._url_decode(value)
        return data

    def _escape_html(self, text):
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def scan_networks(self):
        self.sta.active(True)
        seen = {}
        try:
            raw_networks = self.sta.scan()
        except Exception as exc:
            print("Wi-Fi scan failed: {}".format(exc))
            return []

        for entry in raw_networks:
            try:
                ssid = entry[0].decode().strip()
            except Exception:
                ssid = ""
            if not ssid:
                continue
            rssi = entry[3]
            security = entry[4]
            hidden = entry[5]
            current = seen.get(ssid)
            if current is None or rssi > current["rssi"]:
                seen[ssid] = {
                    "ssid": ssid,
                    "rssi": rssi,
                    "security": security,
                    "hidden": hidden,
                }

        networks = list(seen.values())
        networks.sort(key=lambda item: item["rssi"], reverse=True)
        return networks

    def _network_options_html(self, selected_ssid=""):
        networks = self.scan_networks()
        if not networks:
            return '<option value="">No networks found</option>'

        options = []
        for net in networks:
            label = "{} ({} dBm{})".format(
                net["ssid"],
                net["rssi"],
                ", open" if net["security"] == 0 else "",
            )
            selected = " selected" if net["ssid"] == selected_ssid else ""
            options.append(
                '<option value="{ssid}"{selected}>{label}</option>'.format(
                    ssid=self._escape_html(net["ssid"]),
                    selected=selected,
                    label=self._escape_html(label),
                )
            )
        return "".join(options)

    def _render_page(self, status=None, selected_ssid=""):
        ip = self.ap.ifconfig()[0]
        status_text = self._escape_html(status or self._portal_status)
        options_html = self._network_options_html(selected_ssid)
        current_ssid = self._escape_html(selected_ssid)
        return """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pico Wi-Fi Setup</title>
  <style>
    :root {{
      --bg: #f1efe7;
      --card: #fffaf1;
      --ink: #1d2a2d;
      --muted: #5e6b6f;
      --accent: #1e8f6f;
      --accent-dark: #14664f;
      --border: #d8cfbe;
    }}
    body {{
      margin: 0;
      font-family: Verdana, sans-serif;
      background: radial-gradient(circle at top, #fffdf8, var(--bg));
      color: var(--ink);
    }}
    main {{
      max-width: 34rem;
      margin: 0 auto;
      padding: 1.25rem;
    }}
    .card {{
      background: var(--card);
      border: 2px solid var(--border);
      border-radius: 14px;
      padding: 1rem;
      box-shadow: 0 10px 24px rgba(50, 44, 31, 0.08);
    }}
    h1 {{
      margin-top: 0;
      font-size: 1.4rem;
    }}
    p {{
      line-height: 1.45;
    }}
    .status {{
      padding: 0.75rem;
      border-radius: 10px;
      background: #ebf5f1;
      color: var(--accent-dark);
      margin-bottom: 1rem;
      font-weight: bold;
    }}
    label {{
      display: block;
      margin-top: 0.9rem;
      margin-bottom: 0.35rem;
      font-weight: bold;
    }}
    select, input {{
      width: 100%;
      box-sizing: border-box;
      padding: 0.75rem;
      border: 1px solid var(--border);
      border-radius: 10px;
      font-size: 1rem;
      background: white;
    }}
    button {{
      width: 100%;
      margin-top: 1rem;
      padding: 0.85rem;
      border: 0;
      border-radius: 999px;
      font-size: 1rem;
      font-weight: bold;
      color: white;
      background: var(--accent);
    }}
    .secondary {{
      background: #7b8b8f;
    }}
    .hint {{
      color: var(--muted);
      font-size: 0.95rem;
    }}
  </style>
</head>
<body>
  <main>
    <div class="card">
      <h1>Pico Wi-Fi Setup</h1>
      <p class="status">{status}</p>
      <p>Connected to setup AP at <strong>{ip}</strong>.</p>
      <p class="hint">WPS is not offered here. Select your WLAN manually and enter the password. Hidden networks can be entered by name.</p>
      <form method="post" action="/connect">
        <label for="ssid_select">Detected networks</label>
        <select id="ssid_select" name="ssid_select" onchange="document.getElementById('ssid_manual').value = this.value;">
          {options}
        </select>
        <label for="ssid_manual">SSID</label>
        <input id="ssid_manual" name="ssid" value="{ssid}" placeholder="Your Wi-Fi name">
        <label for="password">Password</label>
        <input id="password" name="password" type="password" placeholder="Leave empty for open networks">
        <button type="submit">Save and connect</button>
      </form>
      <form method="get" action="/">
        <button class="secondary" type="submit">Rescan networks</button>
      </form>
    </div>
  </main>
</body>
</html>""".format(
            status=status_text,
            ip=self._escape_html(ip),
            options=options_html,
            ssid=current_ssid,
        )

    async def _send_response(self, writer, body, status="200 OK", content_type="text/html"):
        response = (
            "HTTP/1.1 {}\r\n"
            "Content-Type: {}; charset=utf-8\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n\r\n{}"
        ).format(status, content_type, len(body), body)
        writer.write(response.encode())
        await writer.drain()

    async def _safe_close_writer(self, writer):
        try:
            writer.close()
        except Exception:
            return
        try:
            await writer.wait_closed()
        except Exception:
            pass

    async def _safe_close_server(self, server):
        try:
            server.close()
        except Exception:
            return
        try:
            await server.wait_closed()
        except Exception:
            pass

    async def _handle_portal_client(self, reader, writer):
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            method, path, _ = request_line.decode().split()

            content_length = 0
            while True:
                line = await reader.readline()
                if not line or line == b"\r\n":
                    break
                if line.lower().startswith(b"content-length:"):
                    content_length = int(line.split(b":", 1)[1].strip())

            if method == "GET" and path == "/":
                body = self._render_page()
                await self._send_response(writer, body)
                return

            if method == "POST" and path == "/connect":
                raw_body = await reader.read(content_length) if content_length else b""
                form = self._parse_form(raw_body.decode())
                ssid = (form.get("ssid") or form.get("ssid_select") or "").strip()
                password = form.get("password", "")
                if not ssid:
                    body = self._render_page("Please select or enter an SSID.")
                    await self._send_response(writer, body, status="400 Bad Request")
                    return

                status = "Connecting to '{}'...".format(ssid)
                await self._send_response(writer, self._render_page(status, ssid))
                await asyncio.sleep_ms(100)

                if self.connect(ssid, password):
                    self.config.set("wifi_ssid", ssid)
                    self.config.set("wifi_password", password)
                    self.config.force_save()
                    self._portal_status = "Connected to '{}'. Rebooting...".format(ssid)
                    self._portal_done = True
                else:
                    self._portal_status = "Connection to '{}' failed. Check password and try again.".format(ssid)
                return

            await self._send_response(writer, "Not Found", status="404 Not Found", content_type="text/plain")
        except Exception as exc:
            print("Wi-Fi portal error: {}".format(exc))
        finally:
            await self._safe_close_writer(writer)

    async def run_setup_portal(self):
        self._portal_done = False
        self.sta.active(False)
        self.ap.active(True)
        self.status_led.value(1)
        ap_ssid = self._ap_ssid()

        if SETUP_AP_PASSWORD:
            self.ap.config(essid=ap_ssid, password=SETUP_AP_PASSWORD, channel=SETUP_AP_CHANNEL)
        else:
            self.ap.config(essid=ap_ssid, channel=SETUP_AP_CHANNEL)

        ap_ip = self.ap.ifconfig()[0]
        print("Wi-Fi setup AP active: {} at {}".format(ap_ssid, ap_ip))
        if SETUP_AP_PASSWORD:
            print("Wi-Fi setup AP password: {}".format(SETUP_AP_PASSWORD))
        else:
            print("Wi-Fi setup AP is open (no password).")
        server = await asyncio.start_server(self._handle_portal_client, "0.0.0.0", 80)

        try:
            while not self._portal_done:
                await asyncio.sleep_ms(200)
        finally:
            await self._safe_close_server(server)
            self.ap.active(False)
            self.status_led.value(0)

        await asyncio.sleep_ms(500)
        machine.reset()
