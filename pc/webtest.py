"""Loopback web page for manually testing an OpenBuddy device over USB."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

from c6lcd.buddy_protocol import VERSION, encode, validate_command
from pc.buddy import find_serial_port, open_serial


PAGE = r"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenBuddy — test ESP32</title>
  <style>
    :root { color-scheme: dark; font: 16px/1.5 system-ui, sans-serif; background: #10131b; color: #edf2ff; }
    body { max-width: 760px; margin: 0 auto; padding: 28px 18px; }
    h1 { margin-bottom: 4px; } .muted { color: #aab4ca; }
    .card { background: #1b2130; border: 1px solid #30394d; border-radius: 14px; padding: 18px; margin: 18px 0; }
    .states { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
    button { border: 0; border-radius: 9px; padding: 12px; background: #536dfe; color: white; font: inherit; font-weight: 650; cursor: pointer; }
    button:hover { filter: brightness(1.13); } button:disabled { opacity: .5; cursor: wait; }
    input { width: 100%; box-sizing: border-box; padding: 10px; border-radius: 8px; border: 1px solid #46516a; background: #111722; color: inherit; font: inherit; }
    label { display: block; margin: 12px 0 5px; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; color: #b7c8ff; }
    #message { min-height: 1.5em; } .error { color: #ff8e9b; } .ok { color: #8fe1ad; }
  </style>
</head>
<body>
  <h1>OpenBuddy — test ESP32</h1>
  <div class="muted">Commandes directes par USB série. Le port reste réservé à cette page.</div>
  <section class="card">
    <h2>État du périphérique</h2>
    <pre id="status">Connexion…</pre>
    <button id="refresh">Actualiser</button>
  </section>
  <section class="card">
    <h2>Envoyer un état</h2>
    <label for="label">Libellé facultatif (24 caractères maximum)</label>
    <input id="label" maxlength="24" placeholder="Test manuel">
    <div class="states" id="states"></div>
    <p id="message" role="status" class="muted"></p>
  </section>
  <script>
    const names = {idle:'Prêt', thinking:'Réflexion', working:'En cours', permission:'Autorisation', question:'Question', success:'Succès', error:'Erreur', retry:'Nouvel essai'};
    const states = document.querySelector('#states');
    const message = document.querySelector('#message');
    for (const [state, title] of Object.entries(names)) {
      const button = document.createElement('button');
      button.textContent = title;
      button.addEventListener('click', () => sendState(state));
      states.append(button);
    }
    async function refresh() {
      const response = await fetch('/api/status');
      const value = await response.json();
      if (!response.ok) throw new Error(value.error || 'Erreur de statut');
      document.querySelector('#status').textContent = JSON.stringify(value, null, 2);
    }
    async function sendState(state) {
      message.textContent = 'Envoi de « ' + names[state] + ' »…';
      message.className = 'muted';
      document.querySelectorAll('button').forEach(button => button.disabled = true);
      try {
        const response = await fetch('/api/state', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({state, label:document.querySelector('#label').value})});
        const value = await response.json();
        if (!response.ok) throw new Error(value.error || 'Commande refusée');
        message.textContent = 'État « ' + names[state] + ' » envoyé.';
        message.className = 'ok';
        await refresh();
      } catch (error) {
        message.textContent = error.message;
        message.className = 'error';
      } finally { document.querySelectorAll('button').forEach(button => button.disabled = false); }
    }
    document.querySelector('#refresh').addEventListener('click', () => refresh().catch(error => { message.textContent = error.message; message.className = 'error'; }));
    refresh().catch(error => { document.querySelector('#status').textContent = error.message; });
  </script>
</body>
</html>
"""


class BuddyDevice:
    """Synchronous, request/acknowledgement access to one USB serial device."""

    def __init__(self, port):
        self.port = port
        self.serial = open_serial(port)
        self.lock = threading.Lock()
        self.sequence = 0

    def close(self):
        self.serial.close()

    def request(self, command, **fields):
        with self.lock:
            self.sequence += 1
            message = dict(fields, v=VERSION, id=self.sequence, cmd=command)
            validate_command(message)
            self.serial.write(encode(message))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                line = self.serial.readline()
                if not line:
                    continue
                if len(line) > 256:
                    continue
                try:
                    response = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(response, dict) or response.get("id") != self.sequence:
                    continue
                if response.get("event") == "error":
                    raise ValueError(response.get("message", "Device rejected command"))
                if response.get("event") == "ack":
                    return response
            raise TimeoutError("Aucun accusé de réception de l’ESP32 sur %s" % self.port)


class WebTestServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, device):
        self.device = device
        super().__init__(address, WebTestHandler)


class WebTestHandler(BaseHTTPRequestHandler):
    server_version = "OpenBuddyTest/1.0"

    def log_message(self, _format, *_args):
        pass

    def _allowed_origin(self):
        host = self.headers.get("Host", "")
        if host not in ("127.0.0.1:%d" % self.server.server_port,
                        "localhost:%d" % self.server.server_port,
                        "[::1]:%d" % self.server.server_port):
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in (
            "http://127.0.0.1:%d" % self.server.server_port,
            "http://localhost:%d" % self.server.server_port,
            "http://[::1]:%d" % self.server.server_port,
        )

    def _send(self, status, value, content_type="application/json; charset=utf-8"):
        body = value.encode("utf-8") if isinstance(value, str) else json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._allowed_origin():
            self._send(403, {"error": "Accès local uniquement"})
        elif self.path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/api/status":
            try:
                self._send(200, self.server.device.request("status"))
            except (OSError, ValueError, TimeoutError) as error:
                self._send(503, {"error": str(error)})
        else:
            self._send(404, {"error": "Introuvable"})

    def do_POST(self):
        if not self._allowed_origin():
            self._send(403, {"error": "Accès local uniquement"})
            return
        if self.path != "/api/state":
            self._send(404, {"error": "Introuvable"})
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._send(415, {"error": "JSON requis"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 < length <= 1024:
                raise ValueError("Taille de requête invalide")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or set(payload) - {"state", "label", "info"}:
                raise ValueError("Paramètres invalides")
            command = dict(payload, cmd="state", id=1, v=VERSION)
            validate_command(command)
            response = self.server.device.request("state", **payload)
            self._send(200, response)
        except (ValueError, UnicodeError, TypeError) as error:
            self._send(400, {"error": str(error)})
        except (OSError, TimeoutError) as error:
            self._send(503, {"error": str(error)})


def main():
    parser = argparse.ArgumentParser(description="Local OpenBuddy USB test page")
    parser.add_argument("--port", default="auto",
                        help="USB serial port (COMx or auto; default: auto)")
    parser.add_argument("--http-port", type=int, default=8765, help="Local HTTP port (default: 8765)")
    args = parser.parse_args()
    serial_port = find_serial_port(args.port)
    device = BuddyDevice(serial_port)
    try:
        server = WebTestServer(("127.0.0.1", args.http_port), device)
    except Exception:
        device.close()
        raise
    print("OpenBuddy test page: http://127.0.0.1:%d/ (device on %s)" %
          (server.server_port, serial_port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping OpenBuddy test page.")
    finally:
        server.server_close()
        device.close()


if __name__ == "__main__":
    main()
