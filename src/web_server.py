import uasyncio as asyncio
import ujson
import uhashlib
import ubinascii
import os
import time
import machine
import boot_log
import slot_manager


class _BodyStream:
    def __init__(self, reader, remaining):
        self.reader = reader
        self.remaining = remaining

    async def read_exact(self, size):
        if size < 0 or size > self.remaining:
            raise ValueError("Unexpected end of request body")

        chunks = bytearray()
        while len(chunks) < size:
            chunk = await self.reader.read(size - len(chunks))
            if not chunk:
                raise ValueError("Request body truncated")
            chunks.extend(chunk)

        self.remaining -= size
        return bytes(chunks)

    async def read_into_file(self, handle, size, hasher=None):
        if size < 0 or size > self.remaining:
            raise ValueError("Unexpected end of request body")

        remaining = size
        while remaining > 0:
            chunk = await self.reader.read(min(1024, remaining))
            if not chunk:
                raise ValueError("Request body truncated")
            if hasher is not None:
                hasher.update(chunk)
            handle.write(chunk)
            remaining -= len(chunk)
            self.remaining -= len(chunk)

    async def discard(self, size, hasher=None):
        if size < 0 or size > self.remaining:
            raise ValueError("Unexpected end of request body")

        remaining = size
        while remaining > 0:
            chunk = await self.reader.read(min(1024, remaining))
            if not chunk:
                raise ValueError("Request body truncated")
            if hasher is not None:
                hasher.update(chunk)
            remaining -= len(chunk)
            self.remaining -= len(chunk)

class WebServer:
    def __init__(self, app_context):
        self.app = app_context # Reference to main App class to trigger actions

    async def _delayed_reset(self, delay_ms=150):
        await asyncio.sleep_ms(delay_ms)
        machine.reset()

    def _octal_field(self, raw):
        raw = raw.split(b"\0", 1)[0].strip() or b"0"
        return int(raw, 8)

    def _join_tar_name(self, header):
        name = header[0:100].split(b"\0", 1)[0].decode()
        prefix = header[345:500].split(b"\0", 1)[0].decode()
        if prefix:
            return prefix + "/" + name
        return name

    def _ensure_dir(self, path):
        parts = [p for p in path.split("/") if p]
        cur = ""
        for part in parts:
            cur = part if not cur else cur + "/" + part
            try:
                os.mkdir(cur)
            except OSError:
                pass

    def _remove_tree(self, path):
        try:
            entries = os.listdir(path)
        except OSError:
            return

        for entry in entries:
            child = path + "/" + entry
            try:
                self._remove_tree(child)
                os.rmdir(child)
            except OSError:
                try:
                    os.remove(child)
                except OSError:
                    pass

        try:
            os.rmdir(path)
        except OSError:
            pass

    def _safe_package_path(self, root, name):
        if not name or name.startswith("/") or ".." in name.split("/"):
            raise ValueError("Invalid package path")
        return root + "/" + name if name else root

    def _release_metadata(self, manifest):
        return {
            "version": manifest.get("version"),
            "build_date_utc": manifest.get("build_date_utc"),
            "git_commit": manifest.get("git_commit"),
            "content_hash": manifest.get("content_hash"),
        }

    async def _extract_package(self, reader, content_length, hasher, staging_root):
        body = _BodyStream(reader, content_length)
        manifest = None
        first_member = True

        while body.remaining > 0:
            header = await body.read_exact(512)
            hasher.update(header)
            if header == (b"\0" * 512):
                await body.discard(body.remaining, hasher=hasher)
                break

            name = self._join_tar_name(header)
            size = self._octal_field(header[124:136])
            typeflag = header[156:157] or b"0"

            if first_member and name != "manifest.json":
                raise ValueError("manifest.json must be the first package member")
            first_member = False

            if typeflag in (b"0", b"\0"):
                if name == "manifest.json":
                    raw = await body.read_exact(size)
                    hasher.update(raw)
                    manifest = ujson.loads(raw.decode())
                else:
                    filepath = self._safe_package_path(staging_root, name)
                    parent = filepath.rsplit("/", 1)[0] if "/" in filepath else staging_root
                    self._ensure_dir(parent)
                    with open(filepath, "wb") as f:
                        await body.read_into_file(f, size, hasher=hasher)
            elif typeflag == b"5":
                self._ensure_dir(self._safe_package_path(staging_root, name))
            else:
                raise ValueError("Unsupported tar entry type")

            pad = (512 - (size % 512)) % 512
            if pad:
                padding = await body.read_exact(pad)
                hasher.update(padding)

        if not manifest:
            raise ValueError("Package manifest missing")

        required_files = manifest.get("required_files") or []
        for relpath in required_files:
            full_path = self._safe_package_path(staging_root, relpath)
            try:
                with open(full_path, "rb"):
                    pass
            except OSError:
                raise ValueError("Missing required file: {}".format(relpath))

        metadata = self._release_metadata(manifest)
        with open(staging_root + "/release_metadata.json", "w") as f:
            ujson.dump(metadata, f)
        return manifest

    async def handle_client(self, reader, writer):
        try:
            request_line = await reader.readline()
            if not request_line:
                writer.close()
                return

            method, path, _ = request_line.decode().split()
            
            # Read headers
            content_length = 0
            headers = {}
            while True:
                line = await reader.readline()
                if not line or line == b'\r\n':
                    break
                line_str = line.decode('utf-8', 'ignore').strip()
                if ':' in line_str:
                    k, v = line_str.split(':', 1)
                    headers[k.lower()] = v.strip()
                    
            content_length = int(headers.get('content-length', 0))

            # Handle requests
            response_body = ""
            status = "200 OK"
            content_type = "text/html; charset=utf-8"

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
            
            elif method == "POST" and path == "/api/debug/enable":
                secret = self.app.config.env.get("UPDATE_SECRET", "default_secret")
                signature = headers.get('x-signature', '')
                
                hasher = uhashlib.sha256()
                hasher.update((secret + "debug").encode())
                expected = ubinascii.hexlify(hasher.digest()).decode()
                
                content_type = "application/json"
                if expected == signature:
                    try:
                        import webrepl
                        webrepl.start()
                        response_body = '{"status": "webrepl_enabled"}'
                    except Exception as e:
                        status = "500 Internal Error"
                        response_body = ujson.dumps({"error": str(e)})
                else:
                    status = "403 Forbidden"
                    response_body = '{"error": "Invalid signature"}'
                    
            elif method == "POST" and path == "/api/upload":
                secret = self.app.config.env.get("UPDATE_SECRET", "default_secret")
                signature = headers.get('x-signature', '')
                filepath = headers.get('x-filepath', '')
                content_type = "application/json"
                
                if not filepath or not signature or '..' in filepath:
                    status = "400 Bad Request"
                    response_body = '{"error": "Missing headers or invalid path"}'
                else:
                    hasher = uhashlib.sha256()
                    hasher.update((secret + filepath).encode())
                    
                    tmp_path = filepath + ".tmp"
                    
                    # Ensure directories exist
                    dir_parts = filepath.split('/')[:-1]
                    cur = ""
                    for p in dir_parts:
                        cur = p if not cur else cur + "/" + p
                        try:
                            os.mkdir(cur)
                        except OSError:
                            pass
                            
                    try:
                        with open(tmp_path, "wb") as f:
                            remaining = content_length
                            while remaining > 0:
                                chunk = await reader.read(min(remaining, 1024))
                                if not chunk: break
                                hasher.update(chunk)
                                f.write(chunk)
                                remaining -= len(chunk)
                        
                        expected = ubinascii.hexlify(hasher.digest()).decode()
                        if expected == signature:
                            try:
                                os.remove(filepath)
                            except OSError:
                                pass
                            os.rename(tmp_path, filepath)
                            response_body = ujson.dumps({"status": "uploaded", "file": filepath})
                        else:
                            os.remove(tmp_path)
                            status = "403 Forbidden"
                            response_body = '{"error": "Invalid signature"}'
                    except Exception as e:
                        status = "500 Internal Error"
                        response_body = ujson.dumps({"error": str(e)})
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
            elif method == "POST" and path == "/api/upload-package":
                secret = self.app.config.env.get("UPDATE_SECRET", "default_secret")
                signature = headers.get("x-signature", "")
                content_type = "application/json"

                if not signature or content_length <= 0:
                    status = "400 Bad Request"
                    response_body = '{"error": "Missing package signature or body"}'
                else:
                    current_slot = slot_manager.normalize_slot(getattr(self.app, "slot_name", slot_manager.DEFAULT_SLOT))
                    target_slot = slot_manager.inactive_slot(current_slot)
                    staging_root = "_staging_" + target_slot

                    print(
                        "OTA package upload starting: current_slot={} target_slot={} bytes={}".format(
                            current_slot, target_slot, content_length
                        )
                    )
                    boot_log.log(
                        "ota package start current={} target={} bytes={}".format(
                            current_slot, target_slot, content_length
                        )
                    )
                    hasher = uhashlib.sha256()
                    hasher.update((secret + "package").encode())

                    self._remove_tree(staging_root)
                    self._ensure_dir(staging_root)
                    if hasattr(self.app, "enter_maintenance_mode"):
                        self.app.enter_maintenance_mode()

                    try:
                        manifest = await self._extract_package(reader, content_length, hasher, staging_root)
                        print(
                            "OTA package extracted: version={} build={} commit={}".format(
                                manifest.get("version"),
                                manifest.get("build_date_utc"),
                                manifest.get("git_commit"),
                            )
                        )
                        boot_log.log("ota package extracted {}".format(manifest.get("version")))
                        expected = ubinascii.hexlify(hasher.digest()).decode()
                        if expected != signature:
                            raise ValueError("Invalid signature")

                        self._remove_tree(target_slot)
                        os.rename(staging_root, target_slot)
                        slot_manager.stage_pending_update(current_slot, target_slot, self._release_metadata(manifest))
                        print("OTA package staged successfully into {}".format(target_slot))
                        boot_log.log("ota package staged {}".format(target_slot))
                        response_body = ujson.dumps({
                            "status": "staged",
                            "target_slot": target_slot,
                            "version": manifest.get("version"),
                        })
                    except ValueError as e:
                        print("OTA package rejected: {}".format(e))
                        boot_log.log("ota package rejected {}".format(e))
                        status = "403 Forbidden" if str(e) == "Invalid signature" else "400 Bad Request"
                        response_body = ujson.dumps({"error": str(e)})
                        self._remove_tree(staging_root)
                        if hasattr(self.app, "exit_maintenance_mode"):
                            self.app.exit_maintenance_mode()
                    except Exception as e:
                        print("OTA package staging failed: {}".format(e))
                        boot_log.log("ota package failed {}".format(e))
                        status = "500 Internal Error"
                        response_body = ujson.dumps({"error": str(e)})
                        self._remove_tree(staging_root)
                        if hasattr(self.app, "exit_maintenance_mode"):
                            self.app.exit_maintenance_mode()
            elif method == "POST" and path == "/api/reset":
                secret = self.app.config.env.get("UPDATE_SECRET", "default_secret")
                signature = headers.get('x-signature', '')

                hasher = uhashlib.sha256()
                hasher.update((secret + "reset").encode())
                expected = ubinascii.hexlify(hasher.digest()).decode()

                content_type = "application/json"
                if expected == signature:
                    response_body = '{"status": "rebooting"}'
                    asyncio.create_task(self._delayed_reset())
                else:
                    status = "403 Forbidden"
                    response_body = '{"error": "Invalid signature"}'
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
    <meta charset="UTF-8">
    <title>Pico LED-Steuerung</title>
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
        button { padding: 12px; width: 100%; margin: 0; background: var(--accent); color: white; border: none; border-radius: 999px; font-weight: bold; cursor: pointer; transition: background 0.2s, transform 0.1s; }
        button:hover { background: #14735b; }
        button:active { transform: scale(0.97); }
        button.active-effect { background: var(--accent-2); box-shadow: 0 0 0 3px rgba(217, 108, 47, 0.3); }
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
        .mini-tools {
            margin-top: 1.5rem;
            padding-top: 0.75rem;
            border-top: 1px dashed var(--line);
            color: var(--muted);
            font-size: 0.78rem;
            display: flex;
            justify-content: flex-end;
            align-items: center;
            gap: 0.55rem;
        }
        .mini-tools label {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            margin: 0;
            font-weight: normal;
            cursor: pointer;
        }
        .mini-tools input[type="checkbox"] {
            width: auto;
            margin: 0;
            accent-color: var(--accent);
        }
    </style>
</head>
<body>
    <main>
        <h1>LED-Steuerung</h1>
        <div class="card">
            <h3>Aktueller Status</h3>
            <pre id="status">Lädt...</pre>
        </div>
        <div class="card">
            <h3>Software</h3>
            <table>
                <tr><th>Slot</th><td id="sw-slot">-</td></tr>
                <tr><th>Version</th><td id="sw-version">-</td></tr>
                <tr><th>Build</th><td id="sw-build">-</td></tr>
                <tr><th>Commit</th><td id="sw-commit">-</td></tr>
                <tr><th>Hash</th><td id="sw-hash">-</td></tr>
                <tr><th>Ausstehend</th><td id="sw-pending">-</td></tr>
            </table>
        </div>
        <div class="card">
            <h3>Effekte</h3>
            <div class="grid">
                <button id="btn-SolidColor" type="button" onclick="setEffect('SolidColor')">Einfarbig</button>
                <button id="btn-LarsonScanner" type="button" onclick="setEffect('LarsonScanner')">Larson-Scanner</button>
                <button id="btn-Rainbow" type="button" onclick="setEffect('Rainbow')">Regenbogen</button>
                <button id="btn-WanderingSpots" type="button" onclick="setEffect('WanderingSpots')">Wandernde Punkte</button>
                <button id="btn-LavaLamp" type="button" onclick="setEffect('LavaLamp')">Lavalampe</button>
                <button id="btn-Sparkle" type="button" onclick="setEffect('Sparkle')">Funkeln</button>
                <button id="btn-Pulse" type="button" onclick="setEffect('Pulse')">Pulsieren</button>
                <button id="btn-FadingSparkle" type="button" onclick="setEffect('FadingSparkle')">Verblassendes Funkeln</button>
            </div>
        </div>
        <div class="card">
            <h3>IR-Fernbedienung Hilfe</h3>
            <p>Die Tabelle unten zeigt die aktuell im Pico-Code implementierten Tastenfunktionen.</p>
            <table>
                <tr><th>Taste</th><th>Aktion</th></tr>
                <tr><td class="key">1</td><td><span class="swatch" style="background:#ff0000;"></span>Rot.</td></tr>
                <tr><td class="key">2</td><td><span class="swatch" style="background:#ff8000;"></span>Orange.</td></tr>
                <tr><td class="key">3</td><td><span class="swatch" style="background:#ffd400;"></span>Gelb.</td></tr>
                <tr><td class="key">4</td><td><span class="swatch" style="background:#00c853;"></span>Grün.</td></tr>
                <tr><td class="key">5</td><td><span class="swatch" style="background:#00d5ff;"></span>Cyan.</td></tr>
                <tr><td class="key">6</td><td><span class="swatch" style="background:#0057ff;"></span>Blau.</td></tr>
                <tr><td class="key">7</td><td><span class="swatch" style="background:#ff00c8;"></span>Magenta.</td></tr>
                <tr><td class="key">8</td><td><span class="swatch" style="background:#8040ff;"></span>Violett.</td></tr>
                <tr><td class="key">9</td><td><span class="swatch" style="background:#ffffff;"></span>Weiß.</td></tr>
                <tr><td class="key">STOP/MODE</td><td>LEDs ein- oder ausschalten.</td></tr>
                <tr><td class="key">SETUP</td><td>Bewegungsmelder-Timeout aktivieren oder deaktivieren.</td></tr>
                <tr><td class="key">ENTER/SAVE</td><td>Nachtmodus aktivieren oder deaktivieren.</td></tr>
                <tr><td class="key">UP</td><td>Nächster Effekt.</td></tr>
                <tr><td class="key">DOWN</td><td>Vorheriger Effekt.</td></tr>
                <tr><td class="key">RIGHT</td><td>Animationsgeschwindigkeit erhöhen.</td></tr>
                <tr><td class="key">LEFT</td><td>Animationsgeschwindigkeit verringern.</td></tr>
                <tr><td class="key">PLAY/PAUSE</td><td>Aktuellen Effekt pausieren oder fortsetzen.</td></tr>
                <tr><td class="key">VOL+</td><td>Helligkeit in festen Schritten erhöhen.</td></tr>
                <tr><td class="key">VOL-</td><td>Helligkeit in festen Schritten verringern.</td></tr>
                <tr><td class="key">0/10+</td><td>Eine Effektinstanz hinzufügen oder einen Wert für unterstützte Effekte erhöhen.</td></tr>
                <tr><td class="key">BACK</td><td>Eine Effektinstanz entfernen oder einen Wert für unterstützte Effekte verringern.</td></tr>
            </table>
        </div>
        <div class="mini-tools">
            <label title="Dauerhafte Boot-Diagnose in boot.log schreiben">
                <input id="boot-log-toggle" type="checkbox" onchange="setBootLog(this.checked)">
                <span>Boot-Protokoll</span>
            </label>
        </div>
    </main>
    <script>
        async function updateStatus() {
            try {
                let res = await fetch('/api/status');
                let data = await res.json();
                document.getElementById('status').innerText = JSON.stringify(data, null, 2);
                document.getElementById('sw-slot').innerText = data.active_slot || '-';
                document.getElementById('sw-version').innerText = data.version || '-';
                document.getElementById('sw-build').innerText = data.build_date_utc || '-';
                document.getElementById('sw-commit').innerText = data.git_commit ? data.git_commit.slice(0, 12) : '-';
                document.getElementById('sw-hash').innerText = data.content_hash ? data.content_hash.slice(0, 16) : '-';
                let pending = '-';
                if (data.pending_slot || data.pending_version) {
                    pending = `${data.pending_slot || '?'} ${data.pending_version || ''}`.trim();
                }
                document.getElementById('sw-pending').innerText = pending;
                document.getElementById('boot-log-toggle').checked = !!data.debug_boot_log;

                document.querySelectorAll('.grid button').forEach(b => b.classList.remove('active-effect'));
                let activeBtn = document.getElementById('btn-' + data.effect);
                if (activeBtn) {
                    activeBtn.classList.add('active-effect');
                }
            } catch(e) {}
        }
        async function setEffect(name) {
            await fetch('/api/config', {
                method: 'POST',
                body: JSON.stringify({effect: name})
            });
            setTimeout(updateStatus, 500);
        }
        async function setBootLog(enabled) {
            await fetch('/api/config', {
                method: 'POST',
                body: JSON.stringify({debug_boot_log: enabled})
            });
            setTimeout(updateStatus, 300);
        }
        setInterval(updateStatus, 2000);
        updateStatus();
    </script>
</body>
</html>"""
