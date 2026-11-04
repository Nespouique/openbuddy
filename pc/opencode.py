"""Read-only OpenCode event bridge. Run: python -m pc.opencode [run|status|stop]."""

import argparse
import asyncio
import contextlib
from dataclasses import dataclass
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time
from urllib.request import ProxyHandler, Request, build_opener

from bleak import BleakClient
from bleak.exc import BleakError

from c6lcd.buddy_protocol import SERVICE_UUID, STATES, TX_UUID
from pc.buddy import Link, UsbLink, find_device, find_serial_port
from pc.notifications import SOUND_ALIASES, SoundNotifier
from pc.quota import QuotaMonitor
from pc.toasts import DesktopNotifier

LEASE_SECONDS = 12
PRIORITY = {"permission": 100, "question": 90, "working": 60, "retry": 55,
            "thinking": 50, "error": 40, "success": 30, "idle": 0}


def discovery_path():
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "openbuddy/bridge.json"


def load_discovery(path):
    if path.stat().st_size > 1024:
        raise ValueError("Invalid OpenBuddy discovery file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (value.get("product") != "openbuddy" or value.get("v") != 1
            or type(value.get("port")) is not int or not 1024 <= value["port"] <= 65535
            or not isinstance(value.get("token"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["token"])):
        raise ValueError("Invalid OpenBuddy discovery file")
    return value


def control(path, action):
    target = load_discovery(path)
    request = Request("http://127.0.0.1:%d/%s" % (target["port"], action),
                      method="POST" if action == "stop" else "GET",
                      headers={"Authorization": "Bearer " + target["token"]},
                      data=b"" if action == "stop" else None)
    # Loopback control requests must never send their token through a corporate proxy.
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=2) as response:
        return json.loads(response.read(4096))


@dataclass
class Lease:
    sequence: int
    received: float
    view: dict


class Registry:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.sources = {}

    def update(self, message):
        if not isinstance(message, dict) or type(message.get("v")) is not int or message["v"] != 1:
            raise ValueError("Invalid state protocol")
        source, sequence = message.get("source"), message.get("sequence")
        if not isinstance(source, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", source):
            raise ValueError("Invalid state source")
        if type(sequence) is not int or not 0 <= sequence <= 2**53 - 1:
            raise ValueError("Invalid state sequence")
        if message.get("state") not in STATES:
            raise ValueError("Invalid state")
        label = message.get("label")
        if not isinstance(label, str) or len(label) > 24 or any(not 32 <= ord(char) <= 126 for char in label):
            raise ValueError("Invalid state label")
        fields = ("pending_permissions", "pending_forms", "active_sessions")
        if any(type(message.get(field)) is not int or not 0 <= message[field] <= 100_000 for field in fields):
            raise ValueError("Invalid state counts")
        self.current()  # Expire old instances before applying a new lease.
        previous = self.sources.get(source)
        if previous is not None and sequence <= previous.sequence:
            return False
        if previous is None and len(self.sources) >= 64:
            raise ValueError("Too many event sources")
        view = {key: message[key] for key in ("state", "label") + fields}
        self.sources[source] = Lease(sequence, self.clock(), view)
        return True

    def current(self):
        now = self.clock()
        for source, lease in list(self.sources.items()):
            if now - lease.received >= LEASE_SECONDS:
                del self.sources[source]
        if not self.sources:
            return None
        leases = list(self.sources.values())
        permissions = sum(item.view["pending_permissions"] for item in leases)
        forms = sum(item.view["pending_forms"] for item in leases)
        if permissions:
            return {"state": "permission", "label": ("%d approval(s) pending" % permissions)[:24]}
        if forms:
            return {"state": "question", "label": ("%d response(s) pending" % forms)[:24]}
        best = max(leases, key=lambda lease: (PRIORITY[lease.view["state"]], lease.received))
        return {key: best.view[key] for key in ("state", "label")}


class Bridge:
    def __init__(self, port=19847, address=None, no_ble=False, path=None, mute=False, no_notifications=False,
                 serial_port=None, no_quota=False, quota_path=None):
        self.port = port
        self.address = address
        self.no_ble = no_ble
        self.serial_port = serial_port
        self.usb_connected = False
        self.path = path or discovery_path()
        self.token = secrets.token_hex(32)
        self.registry = Registry()
        self.stopped = asyncio.Event()
        self.server = None
        self.ble_connected = False
        self.worker = None
        self.last_delivered = None
        self.notifier = SoundNotifier(muted=mute)
        self.desktop_notifier = DesktopNotifier(disabled=no_notifications)
        self.sound_worker = None
        self.quota = QuotaMonitor(quota_path)
        self.no_quota = no_quota
        self.quota_worker = None

    async def start(self):
        if self.path.exists():
            # Refuse unknown files and refuse to replace a live bridge's discovery.
            load_discovery(self.path)
            try:
                await asyncio.to_thread(control, self.path, "health")
            except OSError:
                pass
            else:
                raise ValueError("An OpenBuddy bridge is already running")
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", self.port, limit=8192, backlog=8)
        self.port = self.server.sockets[0].getsockname()[1]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".bridge-", suffix=".json", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"product": "openbuddy", "v": 1, "port": self.port, "token": self.token}, handle)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        if self.serial_port:
            self.worker = asyncio.create_task(self.usb_worker())
        elif not self.no_ble:
            self.worker = asyncio.create_task(self.ble_worker())
        self.sound_worker = asyncio.create_task(self.notification_worker())
        if not self.no_quota:
            self.quota_worker = asyncio.create_task(self.quota.run())
        print("OpenBuddy event bridge listening on 127.0.0.1:%d (read-only)" % self.port, flush=True)

    def health(self):
        view = self.registry.current()
        return {"v": 1, "sources": len(self.registry.sources), "ble_connected": self.ble_connected,
                "usb_connected": self.usb_connected,
                "state": view, "last_delivered": self.last_delivered, "read_only": True,
                "sounds_enabled": self.notifier.enabled,
                "notifications_enabled": self.desktop_notifier.enabled,
                "notifications_last_error": self.desktop_notifier.error,
                "quota": self.quota.text or None, "quota_error": self.quota.error}

    async def handle(self, reader, writer):
        status, response = 400, {"error": "Invalid request"}
        try:
            async with asyncio.timeout(3):
                raw = await reader.readuntil(b"\r\n\r\n")
                lines = raw.decode("ascii").split("\r\n")
                method, path, version = lines[0].split(" ")
                if version != "HTTP/1.1":
                    raise ValueError("Unsupported HTTP version")
                headers = {}
                for line in lines[1:]:
                    if not line:
                        continue
                    key, value = line.split(":", 1)
                    key = key.strip().lower()
                    if key in headers:
                        raise ValueError("Duplicate header")
                    headers[key] = value.strip()
                if headers.get("host") != "127.0.0.1:%d" % self.port or "origin" in headers:
                    status, response = 403, {"error": "Loopback clients only"}
                elif not hmac.compare_digest(headers.get("authorization", ""), "Bearer " + self.token):
                    status, response = 401, {"error": "Authentication required"}
                elif "transfer-encoding" in headers:
                    status, response = 400, {"error": "Chunked requests are not supported"}
                elif method == "GET" and path == "/health":
                    status, response = 200, self.health()
                elif method == "POST" and path == "/stop":
                    status, response = 200, {"stopping": True}
                    self.stopped.set()
                elif method == "POST" and path == "/state":
                    length = int(headers.get("content-length", "-1"))
                    if not 0 < length <= 4096:
                        status, response = 413, {"error": "Invalid body size"}
                    elif headers.get("content-type", "").split(";")[0] != "application/json":
                        status, response = 415, {"error": "JSON required"}
                    else:
                        payload = json.loads(await reader.readexactly(length))
                        accepted = self.registry.update(payload)
                        status, response = 200, {"accepted": accepted}
                else:
                    status, response = 404, {"error": "Unknown endpoint"}
        except (ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            pass
        try:
            body = json.dumps(response).encode()
            writer.write(("HTTP/1.1 %d Result\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % (status, len(body))).encode() + body)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def notification_worker(self):
        # Audio must not depend on the BLE device being connected.
        while True:
            view = self.registry.current()
            self.notifier.observe(view["state"] if view is not None else None)
            self.desktop_notifier.observe(view["state"] if view is not None else None)
            self.quota.observe(view["state"] if view is not None else None)
            await asyncio.sleep(0.25)

    async def ble_worker(self):
        while True:
            link = None
            try:
                device = await find_device(self.address, 5)

                def disconnected(client):
                    if link is not None:
                        link.lost(client)

                async with BleakClient(device, services=[SERVICE_UUID], timeout=30,
                                       disconnected_callback=disconnected) as client:
                    link = Link(client, lambda event: print("Device event:", event.get("event"), "(read-only)", flush=True))
                    await client.start_notify(TX_UUID, link.notification)
                    await link.request("status")
                    self.ble_connected = True
                    print("OpenBuddy BLE connected", flush=True)
                    await self.deliver_states(link)
                    raise ConnectionError("BLE connection closed")
            except (BleakError, ConnectionError, OSError, TimeoutError, ValueError) as error:
                print("BLE unavailable:", type(error).__name__, "- retrying", flush=True)
            finally:
                self.ble_connected = False
            await asyncio.sleep(3)

    async def deliver_states(self, link):
        last, ping_at = None, 0
        while not link.disconnected.is_set():
            view = self.registry.current()
            if view is not None and self.quota.text:
                view = dict(view, info=self.quota.text)
            if view is None:
                # No plugin lease: stop heartbeats so firmware becomes OFFLINE.
                last = None
            elif view != last:
                await link.request("state", **view)
                self.last_delivered = dict(view)
                last, ping_at = view, time.monotonic()
                print("State:", view["state"], "-", view["label"], flush=True)
            elif time.monotonic() - ping_at >= 4:
                await link.request("ping")
                ping_at = time.monotonic()
            await asyncio.sleep(0.25)

    async def usb_worker(self):
        while True:
            link = None
            try:
                port = await asyncio.to_thread(find_serial_port, self.serial_port)
                link = await UsbLink.open(port, lambda event: print("Device event:", event.get("event"), "(read-only)", flush=True))
                await link.request("status")
                self.usb_connected = True
                print("OpenBuddy USB connected on", port, flush=True)
                await self.deliver_states(link)
                raise ConnectionError("USB connection closed")
            except (ConnectionError, OSError, TimeoutError, ValueError) as error:
                print("USB unavailable:", type(error).__name__, "-", error, "- retrying", flush=True)
            finally:
                self.usb_connected = False
                if link is not None:
                    link.close()
            await asyncio.sleep(3)

    async def close(self):
        if self.quota_worker is not None:
            self.quota_worker.cancel()
            await asyncio.gather(self.quota_worker, return_exceptions=True)
        if self.sound_worker is not None:
            self.sound_worker.cancel()
            await asyncio.gather(self.sound_worker, return_exceptions=True)
        if self.worker is not None:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        if self.path.exists():
            try:
                if load_discovery(self.path)["token"] == self.token:
                    self.path.unlink()
            except (OSError, ValueError):
                pass


async def run(args):
    bridge = Bridge(args.port, args.address, args.no_ble, mute=args.mute,
                    no_notifications=args.no_notifications, serial_port=args.serial,
                    no_quota=args.no_quota, quota_path=Path(args.quota_config) if args.quota_config else None)
    try:
        await bridge.start()
        if args.duration is None:
            await bridge.stopped.wait()
        else:
            try:
                await asyncio.wait_for(bridge.stopped.wait(), args.duration)
            except TimeoutError:
                pass
    finally:
        await bridge.close()


async def sound_test(muted):
    notifier = SoundNotifier(muted=muted)
    if not notifier.enabled:
        print("Sound is muted or unsupported on this platform")
        return
    for state, alias in SOUND_ALIASES.items():
        print("Sound:", state, "-", alias, flush=True)
        notifier.observe(state)
        await asyncio.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="Read-only OpenCode v2 to OpenBuddy event bridge")
    parser.add_argument("command", choices=("run", "status", "stop", "sound-test", "notification-test"), nargs="?", default="run")
    parser.add_argument("--port", type=int, default=19847)
    parser.add_argument("--address")
    parser.add_argument("--serial", metavar="PORT", help="Drive the device over its USB serial port (COMx, or auto) instead of Bluetooth")
    parser.add_argument("--no-ble", action="store_true", help="Test the event bridge without the device")
    parser.add_argument("--mute", action="store_true", help="Disable PC sound notifications")
    parser.add_argument("--no-notifications", action="store_true", help="Disable Windows toast notifications, independently of sound")
    parser.add_argument("--no-quota", action="store_true", help="Do not run the provider quota command")
    parser.add_argument("--quota-config", metavar="FILE", help="Quota configuration (default: %%APPDATA%%/openbuddy/quota.json)")
    parser.add_argument("--duration", type=float, help="Stop after this many seconds")
    args = parser.parse_args()
    if not (args.port == 0 or 1024 <= args.port <= 65535) or (args.duration is not None and args.duration <= 0):
        parser.error("Invalid port or duration")
    try:
        if args.command == "run":
            asyncio.run(run(args))
        elif args.command == "sound-test":
            asyncio.run(sound_test(args.mute))
        elif args.command == "notification-test":
            notifier = DesktopNotifier(disabled=args.no_notifications)
            if notifier.post("OpenBuddy — Test", "Les notifications Windows OpenBuddy sont disponibles."):
                print("Windows notification submitted; check the banner or notification center (Win+N).")
            else:
                parser.exit(1, "Windows notification unavailable or disabled\n")
        else:
            print(json.dumps(control(discovery_path(), "health" if args.command == "status" else "stop")))
    except KeyboardInterrupt:
        pass
    except FileNotFoundError as error:
        if args.command in ("status", "stop"):
            parser.exit(1, "No OpenBuddy bridge is running\n")
        parser.exit(1, "Error: %s\n" % error)
    except (OSError, ValueError) as error:
        parser.exit(1, "Error: %s\n" % error)


if __name__ == "__main__":
    main()
