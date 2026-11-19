"""Read-only OpenCode event bridge with integrated local web console and floating pet."""

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
from pc.pet_overlay import OverlayController
from pc.pet_store import BUILTIN_PET_ID, resolve_pet, safe_name
from pc.quota import QuotaMonitor
from pc.toasts import DesktopNotifier
from pc.web_admin import DEFAULT_WEB_PORT, WebAdminRunner

LEASE_SECONDS = 12
PRIORITY = {"permission": 100, "question": 90, "working": 60, "retry": 55,
            "thinking": 50, "error": 40, "success": 30, "idle": 0}
VALID_TRANSPORTS = ("usb", "ble", "none")


def discovery_path():
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "openbuddy/bridge.json"


def bridge_config_path():
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "openbuddy/bridge-config.json"


def default_bridge_config():
    return {
        "transport": "ble",
        "serial_port": "auto",
        "ble_address": "",
        "sounds_enabled": True,
        "notifications_enabled": True,
        "quota_enabled": True,
        "pet": "auto",
        "overlay_scale": 1,
    }


def validate_bridge_config(updates):
    """Validate partial or complete bridge configuration fields."""
    if not isinstance(updates, dict):
        raise ValueError("Configuration must be a JSON object")
    allowed = set(default_bridge_config())
    unknown = set(updates) - allowed
    if unknown:
        raise ValueError("Unknown configuration field(s): " + ", ".join(sorted(unknown)))
    cleaned = {}
    if "transport" in updates:
        value = updates["transport"]
        if value not in VALID_TRANSPORTS:
            raise ValueError("Invalid transport: must be usb, ble, or none")
        cleaned["transport"] = value
    if "serial_port" in updates:
        value = updates["serial_port"]
        if not isinstance(value, str) or len(value.strip()) > 64:
            raise ValueError("Invalid serial_port")
        cleaned["serial_port"] = value.strip() or "auto"
    if "ble_address" in updates:
        value = updates["ble_address"]
        if not isinstance(value, str) or len(value.strip()) > 64:
            raise ValueError("Invalid ble_address")
        cleaned["ble_address"] = value.strip()
    for flag in ("sounds_enabled", "notifications_enabled", "quota_enabled"):
        if flag in updates:
            if type(updates[flag]) is not bool:
                raise ValueError("%s must be a boolean" % flag)
            cleaned[flag] = updates[flag]
    if "pet" in updates:
        value = updates["pet"]
        if not isinstance(value, str) or (value not in ("auto", BUILTIN_PET_ID) and not safe_name(value)):
            raise ValueError("Invalid pet selection")
        cleaned["pet"] = value
    if "overlay_scale" in updates:
        value = updates["overlay_scale"]
        if type(value) is not int or value not in (1, 2):
            raise ValueError("overlay_scale must be 1 or 2")
        cleaned["overlay_scale"] = value
    return cleaned


def load_bridge_config(path):
    config = default_bridge_config()
    if path is None or not Path(path).is_file():
        return config
    try:
        if Path(path).stat().st_size > 8192:
            return config
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            subset = {k: v for k, v in raw.items() if k in config}
            config.update(validate_bridge_config(subset))
    except (OSError, ValueError, TypeError):
        pass
    return config


def save_bridge_config(path, config):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".bridge-config-", suffix=".json", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


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
                 serial_port=None, no_quota=False, quota_path=None, web_port=None, config_path=None,
                 pet="auto", overlay_scale=1, pet_roots=None, overlay_controller=None,
                 port_lister=None, start_overlay=False):
        self.port = port
        self.address = address or ""
        self.no_ble = no_ble
        self.serial_port = serial_port
        self.transport = "usb" if serial_port else ("none" if no_ble else "ble")
        self.serial_port_setting = serial_port or "auto"
        self.serial_port_active = None
        self.usb_connected = False
        self.path = path or discovery_path()
        self.config_path = config_path
        self.token = secrets.token_hex(32)
        self.registry = Registry()
        self.manual_state = None
        self.stopped = asyncio.Event()
        self.server = None
        self.loop = None
        self.ble_connected = False
        self.worker = None
        self.active_link = None
        self.device_status = None
        self.last_delivered = None
        self.notifier = SoundNotifier(muted=mute)
        self.desktop_notifier = DesktopNotifier(disabled=no_notifications)
        self.sound_worker = None
        self.quota = QuotaMonitor(quota_path)
        self.no_quota = no_quota
        self.quota_worker = None
        self.pet_selection = pet
        self.overlay_scale = 2 if overlay_scale == 2 else 1
        self.pet_roots = pet_roots
        self.web_port_requested = web_port
        self.web_port = None
        self.web_runner = None
        self.port_lister = port_lister
        self.start_overlay = start_overlay
        self.overlay = overlay_controller if overlay_controller is not None else OverlayController(
            get_snapshot=self._overlay_snapshot,
            on_scale_change=self._handle_overlay_scale_change,
            web_url_provider=lambda: ("http://127.0.0.1:%d/" % self.web_port) if self.web_port else None,
            pet_roots=self.pet_roots,
        )

    def _overlay_snapshot(self):
        view = self.effective_view()
        if view is None:
            return {
                "state": "offline",
                "label": "OpenCode inactif",
                "info": self.quota.text or "",
                "pet": self.pet_selection,
                "overlay_scale": self.overlay_scale,
            }
        return {
            "state": view["state"],
            "label": view.get("label", ""),
            "info": view.get("info") or self.quota.text or "",
            "pet": self.pet_selection,
            "overlay_scale": self.overlay_scale,
        }

    def _handle_overlay_scale_change(self, scale):
        if self.loop is not None and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self.apply_config({"overlay_scale": scale}, persist=True),
                self.loop,
            )

    def effective_view(self):
        if self.manual_state is not None:
            return dict(self.manual_state)
        return self.registry.current()

    def get_config(self):
        return {
            "transport": self.transport,
            "serial_port": self.serial_port_setting,
            "ble_address": self.address,
            "sounds_enabled": self.notifier.enabled,
            "notifications_enabled": self.desktop_notifier.enabled,
            "quota_enabled": not self.no_quota,
            "pet": self.pet_selection,
            "overlay_scale": self.overlay_scale,
        }

    def get_pet_pack(self, pet_id):
        _, _, packs = resolve_pet("auto", self.pet_roots)
        return packs.get(pet_id)

    def _pet_payload(self):
        active_id, pack, packs = resolve_pet(self.pet_selection, self.pet_roots)
        available = [
            {"id": p.pet_id, "builtin": False, "author": p.author}
            for p in packs.values()
        ]
        available.append({"id": BUILTIN_PET_ID, "builtin": True, "author": "OpenBuddy"})
        if pack is not None:
            meta = pack.metadata()
        else:
            meta = {"id": BUILTIN_PET_ID, "builtin": True, "width": 96, "height": 104}
        meta["selected"] = self.pet_selection
        meta["active_id"] = active_id
        meta["available"] = available
        return meta

    def dashboard_snapshot(self):
        eff = self.effective_view()
        if eff is not None and not eff.get("info") and self.quota.text:
            eff = dict(eff, info=self.quota.text)
        bridge_info = self.health()
        bridge_info.update({
            "bridge_port": self.port,
            "web_port": self.web_port,
            "transport": self.transport,
            "serial_port_active": self.serial_port_active,
            "device_status": self.device_status,
            "effective_state": eff,
            "manual_mode": self.manual_state is not None,
        })
        return {
            "bridge": bridge_info,
            "config": self.get_config(),
            "overlay": self.overlay.status(),
            "pet": self._pet_payload(),
        }

    async def _restart_transport_worker(self):
        if self.worker is not None:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None
        self.active_link = None
        self.device_status = None
        self.serial_port_active = None
        self.usb_connected = False
        self.ble_connected = False
        if self.transport == "usb":
            self.serial_port = self.serial_port_setting or "auto"
            self.no_ble = False
            self.worker = asyncio.create_task(self.usb_worker())
        elif self.transport == "ble":
            self.serial_port = None
            self.no_ble = False
            self.worker = asyncio.create_task(self.ble_worker())
        else:
            self.serial_port = None
            self.no_ble = True

    async def _sync_quota_worker(self):
        if self.no_quota:
            if self.quota_worker is not None:
                self.quota_worker.cancel()
                await asyncio.gather(self.quota_worker, return_exceptions=True)
                self.quota_worker = None
            self.quota.text = ""
            self.quota.error = None
        elif self.quota_worker is None:
            self.quota_worker = asyncio.create_task(self.quota.run())

    async def apply_config(self, updates, persist=True):
        cleaned = validate_bridge_config(updates)
        transport_changed = False
        if "transport" in cleaned and cleaned["transport"] != self.transport:
            self.transport = cleaned["transport"]
            transport_changed = True
        if "serial_port" in cleaned and cleaned["serial_port"] != self.serial_port_setting:
            self.serial_port_setting = cleaned["serial_port"]
            if self.transport == "usb":
                transport_changed = True
        if "ble_address" in cleaned and cleaned["ble_address"] != self.address:
            self.address = cleaned["ble_address"]
            if self.transport == "ble":
                transport_changed = True
        if "sounds_enabled" in cleaned:
            self.notifier.set_enabled(cleaned["sounds_enabled"])
        if "notifications_enabled" in cleaned:
            self.desktop_notifier.set_enabled(cleaned["notifications_enabled"])
        if "quota_enabled" in cleaned:
            self.no_quota = not cleaned["quota_enabled"]
            await self._sync_quota_worker()
        if "pet" in cleaned:
            self.pet_selection = cleaned["pet"]
        if "overlay_scale" in cleaned:
            self.overlay_scale = cleaned["overlay_scale"]
        if transport_changed and self.server is not None:
            await self._restart_transport_worker()
        if persist and self.config_path is not None:
            await asyncio.to_thread(save_bridge_config, self.config_path, self.get_config())
        return self.dashboard_snapshot()

    async def set_manual_state(self, payload):
        entry = {"state": payload["state"], "label": payload.get("label", "")}
        if payload.get("info"):
            entry["info"] = payload["info"]
        self.manual_state = entry
        return self.dashboard_snapshot()

    async def clear_manual_state(self):
        self.manual_state = None
        return self.dashboard_snapshot()

    async def set_overlay(self, payload):
        if "scale" in payload:
            await self.apply_config({"overlay_scale": payload["scale"]}, persist=True)
        if payload.get("toggle"):
            target = not self.overlay.visible
        elif "visible" in payload:
            if type(payload["visible"]) is not bool:
                raise ValueError("visible must be a boolean")
            target = payload["visible"]
        else:
            target = self.overlay.visible
        await asyncio.to_thread(self.overlay.set_visible, target)
        return self.dashboard_snapshot()

    async def test_sound(self, state="permission"):
        return await asyncio.to_thread(self.notifier.play, state)

    async def test_notification(self):
        return await asyncio.to_thread(
            self.desktop_notifier.post,
            "OpenBuddy — Test",
            "Les notifications Windows OpenBuddy sont disponibles.",
        )

    async def refresh_quota_now(self):
        if not self.no_quota:
            await self.quota.refresh()
        return self.dashboard_snapshot()

    async def refresh_device_status(self):
        if self.active_link is None:
            raise ConnectionError("Aucun ESP32 connecté actuellement")
        status = await self.active_link.request("status")
        self.device_status = status
        return self.dashboard_snapshot()

    async def start(self):
        self.loop = asyncio.get_running_loop()
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
        if self.web_port_requested is not None:
            self.web_runner = WebAdminRunner(
                self, self.loop, port=self.web_port_requested, port_lister=self.port_lister,
            )
            self.web_runner.start()
            self.web_port = self.web_runner.port
        await self._restart_transport_worker()
        self.sound_worker = asyncio.create_task(self.notification_worker())
        await self._sync_quota_worker()
        if self.start_overlay:
            await asyncio.to_thread(self.overlay.show)
        print("OpenBuddy event bridge listening on 127.0.0.1:%d (read-only)" % self.port, flush=True)
        if self.web_port is not None:
            print("OpenBuddy web console: http://127.0.0.1:%d/" % self.web_port, flush=True)

    def health(self):
        view = self.registry.current()
        return {"v": 1, "sources": len(self.registry.sources), "ble_connected": self.ble_connected,
                "usb_connected": self.usb_connected,
                "state": view, "last_delivered": self.last_delivered, "read_only": True,
                "sounds_enabled": self.notifier.enabled,
                "notifications_enabled": self.desktop_notifier.enabled,
                "notifications_last_error": self.desktop_notifier.error,
                "quota": self.quota.text or None, "quota_error": self.quota.error,
                "web_port": self.web_port, "transport": self.transport}

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
                device = await find_device(self.address or None, 5)

                def disconnected(client):
                    if link is not None:
                        link.lost(client)

                async with BleakClient(device, services=[SERVICE_UUID], timeout=30,
                                       disconnected_callback=disconnected) as client:
                    link = Link(client, lambda event: print("Device event:", event.get("event"), "(read-only)", flush=True))
                    await client.start_notify(TX_UUID, link.notification)
                    self.device_status = await link.request("status")
                    self.active_link = link
                    self.ble_connected = True
                    print("OpenBuddy BLE connected", flush=True)
                    await self.deliver_states(link)
                    raise ConnectionError("BLE connection closed")
            except (BleakError, ConnectionError, OSError, TimeoutError, ValueError) as error:
                print("BLE unavailable:", type(error).__name__, "- retrying", flush=True)
            finally:
                self.ble_connected = False
                if self.active_link is link:
                    self.active_link = None
            await asyncio.sleep(3)

    async def deliver_states(self, link):
        last, ping_at = None, 0
        while not link.disconnected.is_set():
            view = self.effective_view()
            if view is not None and not view.get("info") and self.quota.text:
                view = dict(view, info=self.quota.text)
            if view is None:
                # No plugin lease and no manual preview: stop heartbeats so firmware becomes OFFLINE.
                last = None
            elif view != last:
                await link.request("state", **view)
                self.last_delivered = dict(view)
                last, ping_at = view, time.monotonic()
                print("State:", view["state"], "-", view.get("label", ""), flush=True)
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
                self.device_status = await link.request("status")
                self.serial_port_active = port
                self.active_link = link
                self.usb_connected = True
                print("OpenBuddy USB connected on", port, flush=True)
                await self.deliver_states(link)
                raise ConnectionError("USB connection closed")
            except (ConnectionError, OSError, TimeoutError, ValueError) as error:
                print("USB unavailable:", type(error).__name__, "-", error, "- retrying", flush=True)
            finally:
                self.usb_connected = False
                self.serial_port_active = None
                if self.active_link is link:
                    self.active_link = None
                if link is not None:
                    link.close()
            await asyncio.sleep(3)

    async def close(self):
        await asyncio.to_thread(self.overlay.close)
        if self.web_runner is not None:
            await asyncio.to_thread(self.web_runner.close)
            self.web_runner = None
            self.web_port = None
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


def build_bridge_from_args(args, cfg_path=None):
    """Merge persisted bridge-config.json with explicit CLI flags."""
    config_file = cfg_path if cfg_path is not None else bridge_config_path()
    saved = load_bridge_config(config_file)
    transport = saved["transport"]
    serial_port = saved["serial_port"] if transport == "usb" else None
    address = saved["ble_address"] or None
    no_ble = transport == "none"

    if args.serial is not None:
        serial_port = args.serial
        no_ble = False
    elif args.no_ble:
        serial_port = None
        no_ble = True
    elif args.address is not None:
        serial_port = None
        no_ble = False

    if args.address is not None:
        address = args.address

    mute = True if args.mute else (not saved["sounds_enabled"])
    no_notifications = True if args.no_notifications else (not saved["notifications_enabled"])
    no_quota = True if args.no_quota else (not saved["quota_enabled"])
    web_port = None if args.no_web else args.web_port

    return Bridge(
        port=args.port,
        address=address,
        no_ble=no_ble,
        mute=mute,
        no_notifications=no_notifications,
        serial_port=serial_port,
        no_quota=no_quota,
        quota_path=Path(args.quota_config) if args.quota_config else None,
        web_port=web_port,
        config_path=config_file,
        pet=saved["pet"],
        overlay_scale=saved["overlay_scale"],
        start_overlay=args.overlay,
    )


async def run(args):
    bridge = build_bridge_from_args(args)
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
    parser.add_argument("--no-ble", action="store_true", help="Run the bridge and virtual pet without an ESP32 device")
    parser.add_argument("--mute", action="store_true", help="Disable PC sound notifications")
    parser.add_argument("--no-notifications", action="store_true", help="Disable Windows toast notifications, independently of sound")
    parser.add_argument("--no-quota", action="store_true", help="Do not run the provider quota command")
    parser.add_argument("--quota-config", metavar="FILE", help="Quota configuration (default: %%APPDATA%%/openbuddy/quota.json)")
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT, help="Local web console HTTP port (default: 8765)")
    parser.add_argument("--no-web", action="store_true", help="Disable the local web console")
    parser.add_argument("--overlay", action="store_true", help="Show the floating desktop pet window on startup")
    parser.add_argument("--duration", type=float, help="Stop after this many seconds")
    args = parser.parse_args()
    if (not (args.port == 0 or 1024 <= args.port <= 65535)
            or not (args.web_port == 0 or 1024 <= args.web_port <= 65535)
            or (args.duration is not None and args.duration <= 0)):
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
