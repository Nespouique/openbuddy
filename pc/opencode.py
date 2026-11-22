"""Read-only OpenCode event bridge with integrated local web console and floating pet."""

import argparse
import asyncio
import contextlib
import ctypes
from dataclasses import dataclass
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import struct
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, Request, build_opener

from c6lcd.buddy_protocol import SERVICE_UUID, STATES, TX_UUID
from pc.buddy import Link, UsbLink, find_device, find_serial_port, has_bleak
from pc.notifications import SOUND_ALIASES, SoundNotifier
from pc.pet import (CLIP_LABELS, CLIPS, STATE_CLIPS, clip_frames, convert,
                    default_states_for_clips, fetch_codex_pet, has_pillow,
                    write_pack_from_clips)
from pc.pet_overlay import OverlayController
from pc.pet_store import (BUILTIN_PET_ID, REQUIRED_STATES, default_pet_roots,
                          delete_pack, resolve_pet, safe_name,
                          update_pack_states, user_pets_dir)
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
        "transport": "usb",
        "serial_port": "auto",
        "ble_address": "",
        "sounds_enabled": True,
        "notifications_enabled": True,
        "quota_enabled": True,
        "require_opencode_client": True,
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
    for flag in ("sounds_enabled", "notifications_enabled", "quota_enabled", "require_opencode_client"):
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


def is_background_service_command(cmdline):
    """Return True when an opencode command line is the background daemon or service helper."""
    if not isinstance(cmdline, str) or not cmdline.strip():
        return False
    try:
        tokens = [tok.strip("\"'").lower() for tok in shlex.split(cmdline, posix=False)]
    except ValueError:
        tokens = [tok.strip("\"'").lower() for tok in cmdline.split()]
    args = tokens[1:]
    if "--service" in args:
        return True
    for token in args:
        if token.startswith("-"):
            continue
        return token in ("serve", "service")
    return False


def _read_process_cmdline_windows(pid):
    """Read a same-user Windows process command line via PEB without spawning a subprocess."""
    from ctypes import wintypes

    class PROCESS_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("Reserved1", ctypes.c_void_p),
            ("PebBaseAddress", ctypes.c_void_p),
            ("Reserved2", ctypes.c_void_p * 2),
            ("UniqueProcessId", ctypes.c_void_p),
            ("Reserved3", ctypes.c_void_p),
        ]

    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", ctypes.c_void_p),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    nt = ctypes.WinDLL("ntdll")
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.ReadProcessMemory.restype = wintypes.BOOL
    k32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    ]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    nt.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p,
        wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
    ]

    handle = k32.OpenProcess(0x0410, False, pid)  # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
    if not handle:
        return None
    try:
        pbi = PROCESS_BASIC_INFORMATION()
        needed = wintypes.ULONG()
        if nt.NtQueryInformationProcess(handle, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(needed)) != 0:
            return None
        if not pbi.PebBaseAddress:
            return None
        is_64 = struct.calcsize("P") == 8
        params_offset = 0x20 if is_64 else 0x10
        cmdline_offset = 0x70 if is_64 else 0x40
        params_ptr = ctypes.c_void_p()
        read = ctypes.c_size_t()
        if not k32.ReadProcessMemory(handle, pbi.PebBaseAddress + params_offset,
                                     ctypes.byref(params_ptr), ctypes.sizeof(params_ptr), ctypes.byref(read)):
            return None
        if not params_ptr.value:
            return None
        ustr = UNICODE_STRING()
        if not k32.ReadProcessMemory(handle, params_ptr.value + cmdline_offset,
                                     ctypes.byref(ustr), ctypes.sizeof(ustr), ctypes.byref(read)):
            return None
        if not ustr.Length or not ustr.Buffer or ustr.Length > 32768:
            return ""
        buf = ctypes.create_unicode_buffer(ustr.Length // 2 + 1)
        if not k32.ReadProcessMemory(handle, ustr.Buffer, buf, ustr.Length, ctypes.byref(read)):
            return None
        return buf.value
    finally:
        k32.CloseHandle(handle)


def _list_opencode_pids_windows():
    """Return PIDs of running opencode*.exe processes via Toolhelp32."""
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return None
    pids = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if k32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                exe = (entry.szExeFile or "").lower()
                if exe == "opencode.exe" or (exe.startswith("opencode-") and exe.endswith(".exe")):
                    pids.append(entry.th32ProcessID)
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        k32.CloseHandle(snap)
    return pids


def has_interactive_opencode_client():
    """Return True if an interactive OpenCode client is running, False if none, or None if unknown."""
    if sys.platform == "win32":
        try:
            pids = _list_opencode_pids_windows()
            if pids is None:
                return None
            if not pids:
                return False
            unreadable = 0
            for pid in pids:
                cmdline = _read_process_cmdline_windows(pid)
                if cmdline is None:
                    unreadable += 1
                    continue
                if not is_background_service_command(cmdline):
                    return True
            if unreadable == 0:
                return False
            return True if len(pids) >= 2 else None
        except Exception:
            return None
    if sys.platform.startswith("linux"):
        try:
            proc = Path("/proc")
            if not proc.is_dir():
                return None
            found_any = False
            for entry in proc.iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    raw = (entry / "cmdline").read_bytes()
                except OSError:
                    continue
                if not raw:
                    continue
                parts = [p.decode("utf-8", errors="ignore") for p in raw.split(b"\x00") if p]
                if not parts:
                    continue
                exe = Path(parts[0]).name.lower()
                if exe == "opencode" or exe.startswith("opencode-"):
                    found_any = True
                    if not is_background_service_command(" ".join(parts)):
                        return True
            return False if found_any else False
        except Exception:
            return None
    return None


class Bridge:
    def __init__(self, port=19847, address=None, no_ble=False, path=None, mute=False, no_notifications=False,
                 serial_port=None, no_quota=False, quota_path=None, web_port=None, config_path=None,
                 pet="auto", overlay_scale=1, pet_roots=None, overlay_controller=None,
                 port_lister=None, start_overlay=False, pet_fetcher=None,
                 require_opencode_client=True, client_checker=None, transport=None):
        self.port = port
        self.address = address or ""
        self.no_ble = no_ble
        if transport in VALID_TRANSPORTS:
            self.transport = transport
        elif serial_port:
            self.transport = "usb"
        elif no_ble:
            self.transport = "none"
        elif address:
            self.transport = "ble"
        else:
            self.transport = "usb"
        self.serial_port_setting = serial_port or "auto"
        self.serial_port = self.serial_port_setting if self.transport == "usb" else None
        self.serial_port_active = None
        self.usb_connected = False
        self.ble_connected = False
        self.path = path or discovery_path()
        self.config_path = config_path
        self.token = secrets.token_hex(32)
        self.registry = Registry()
        self.manual_state = None
        self.stopped = asyncio.Event()
        self.server = None
        self.loop = None
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
        self.require_opencode_client = bool(require_opencode_client)
        self.client_checker = client_checker if client_checker is not None else has_interactive_opencode_client
        self._client_check_at = -float("inf")
        self._client_check_result = None
        self.pet_selection = pet
        self.overlay_scale = 2 if overlay_scale == 2 else 1
        self.pet_roots = pet_roots
        self.pet_fetcher = pet_fetcher or fetch_codex_pet
        self._staged_imports = {}
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

    def _pet_collection_root(self):
        if self.pet_roots:
            return Path(self.pet_roots[0])
        return user_pets_dir()

    def _pet_cache_root(self):
        if self.pet_roots:
            return Path(self.pet_roots[0]).parent / ".pet-cache"
        return user_pets_dir().parent / "cache/pets"

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

    def _check_opencode_client(self):
        now = time.monotonic()
        if now - self._client_check_at >= 1.0:
            try:
                self._client_check_result = self.client_checker() if self.client_checker is not None else None
            except Exception:
                self._client_check_result = None
            self._client_check_at = now
        return self._client_check_result

    def effective_view(self):
        if self.manual_state is not None:
            return dict(self.manual_state)
        view = self.registry.current()
        if view is None:
            return None
        if self.require_opencode_client and view.get("state") == "idle":
            if self._check_opencode_client() is False:
                return None
        return view

    def get_config(self):
        return {
            "transport": self.transport,
            "serial_port": self.serial_port_setting,
            "ble_address": self.address,
            "sounds_enabled": self.notifier.enabled,
            "notifications_enabled": self.desktop_notifier.enabled,
            "quota_enabled": not self.no_quota,
            "require_opencode_client": self.require_opencode_client,
            "pet": self.pet_selection,
            "overlay_scale": self.overlay_scale,
        }

    def get_pet_pack(self, pet_id):
        _, _, packs = resolve_pet("auto", self.pet_roots)
        return packs.get(pet_id)

    def _pet_payload(self):
        active_id, pack, packs = resolve_pet(self.pet_selection, self.pet_roots)
        allowed_roots = [Path(r).resolve() for r in (default_pet_roots() if self.pet_roots is None else self.pet_roots)]
        available = []
        for p in packs.values():
            entry = p.metadata()
            entry["deletable"] = p.directory.resolve().parent in allowed_roots
            available.append(entry)
        available.append({
            "id": BUILTIN_PET_ID,
            "name": "Robot intégré",
            "builtin": True,
            "author": "OpenBuddy",
            "source_url": "",
            "width": 96,
            "height": 104,
            "states": {},
            "animations": {},
            "deletable": False,
        })
        if pack is not None:
            meta = pack.metadata()
        else:
            meta = {"id": BUILTIN_PET_ID, "name": "Robot intégré", "builtin": True, "width": 96, "height": 104}
        meta["selected"] = self.pet_selection
        meta["active_id"] = active_id
        meta["available"] = available
        meta["clip_labels"] = dict(CLIP_LABELS)
        meta["required_states"] = list(REQUIRED_STATES)
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

    async def import_pet_from_url(self, url, activate=True, states=None):
        """Download and convert a Codex Pets URL into the local collection."""
        pet_id, sheet_path, source, pet_meta = await asyncio.to_thread(
            self.pet_fetcher, url, self._pet_cache_root()
        )
        output_dir = self._pet_collection_root() / pet_id
        if has_pillow():
            await asyncio.to_thread(
                convert, sheet_path, output_dir, pet_id, source, None, True, states
            )
            if activate:
                await self.apply_config({"pet": pet_id}, persist=True)
            return {
                "status": "imported",
                "pet_id": pet_id,
                "dashboard": self.dashboard_snapshot(),
            }
        # Pillow is not installed: stage the downloaded spritesheet for browser-side WebP decode.
        self._staged_imports[pet_id] = {
            "pet_id": pet_id,
            "sheet_path": Path(sheet_path),
            "source": source,
            "output_dir": output_dir,
            "activate": bool(activate),
            "clips": {},
            "created": time.monotonic(),
        }
        return {
            "status": "needs_client_decode",
            "pet_id": pet_id,
            "name": source.get("name") or pet_id,
            "author": source.get("author") or "",
            "sheet_url": "/api/pets/staged/%s/spritesheet" % pet_id,
            "clips": {name: [list(seg) for seg in segments] for name, segments in CLIPS.items()},
            "default_states": dict(STATE_CLIPS),
        }

    def get_staged_spritesheet(self, pet_id):
        staged = self._staged_imports.get(pet_id)
        if staged is None:
            raise KeyError(pet_id)
        return staged["sheet_path"].read_bytes()

    def store_staged_clip(self, pet_id, clip_name, raw_bytes):
        staged = self._staged_imports.get(pet_id)
        if staged is None:
            raise KeyError("Import non trouvé : " + pet_id)
        if clip_name not in CLIPS:
            raise ValueError("Clip inconnu : " + clip_name)
        expected_frames = clip_frames(CLIPS[clip_name])
        expected_bytes = expected_frames * 96 * 104 * 2
        if len(raw_bytes) != expected_bytes:
            raise ValueError("Taille de clip invalide pour %s (%d != %d)" % (clip_name, len(raw_bytes), expected_bytes))
        staged["clips"][clip_name] = (bytes(raw_bytes), expected_frames)
        return {"stored": clip_name, "bytes": len(raw_bytes)}

    async def commit_staged_import(self, pet_id, states=None, activate=None):
        staged = self._staged_imports.get(pet_id)
        if staged is None:
            raise ValueError("Aucun import en cours pour " + pet_id)
        if "idle" not in staged["clips"]:
            raise ValueError("Le clip 'idle' est obligatoire")
        await asyncio.to_thread(
            write_pack_from_clips,
            staged["output_dir"],
            pet_id,
            staged["source"],
            staged["clips"],
            states=states,
            force=True,
        )
        should_activate = staged["activate"] if activate is None else bool(activate)
        self._staged_imports.pop(pet_id, None)
        if should_activate:
            await self.apply_config({"pet": pet_id}, persist=True)
        return {
            "status": "imported",
            "pet_id": pet_id,
            "dashboard": self.dashboard_snapshot(),
        }

    async def update_pet_mapping(self, pet_id, states=None, reset=False):
        pack = self.get_pet_pack(pet_id)
        if pack is None:
            raise ValueError("Pet introuvable dans la collection : " + str(pet_id))
        target_states = default_states_for_clips(pack.animations.keys()) if reset else states
        await asyncio.to_thread(update_pack_states, pack, target_states)
        return self.dashboard_snapshot()

    async def delete_pet(self, pet_id):
        if pet_id == BUILTIN_PET_ID:
            raise ValueError("Impossible de supprimer le robot intégré")
        pack = self.get_pet_pack(pet_id)
        if pack is None:
            raise ValueError("Pet introuvable dans la collection : " + str(pet_id))
        allowed = default_pet_roots() if self.pet_roots is None else self.pet_roots
        await asyncio.to_thread(delete_pack, pack, allowed)
        if self.pet_selection == pet_id:
            await self.apply_config({"pet": "auto"}, persist=True)
        return self.dashboard_snapshot()

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
        if "require_opencode_client" in cleaned:
            self.require_opencode_client = cleaned["require_opencode_client"]
            self._client_check_at = -float("inf")
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
        client_active = self._check_opencode_client() if self.require_opencode_client else None
        return {"v": 1, "sources": len(self.registry.sources), "ble_connected": self.ble_connected,
                "ble_available": has_bleak(),
                "usb_connected": self.usb_connected,
                "state": view, "last_delivered": self.last_delivered, "read_only": True,
                "sounds_enabled": self.notifier.enabled,
                "notifications_enabled": self.desktop_notifier.enabled,
                "notifications_last_error": self.desktop_notifier.error,
                "quota": self.quota.text or None, "quota_error": self.quota.error,
                "opencode_client_active": client_active,
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
        # Audio and desktop notifications never depend on an attached ESP32.
        while True:
            view = self.registry.current()
            self.notifier.observe(view["state"] if view is not None else None)
            self.desktop_notifier.observe(view["state"] if view is not None else None)
            self.quota.observe(view["state"] if view is not None else None)
            await asyncio.sleep(0.25)

    async def ble_worker(self):
        try:
            from bleak import BleakClient
            from bleak.exc import BleakError
        except ImportError:
            print("BLE unavailable: optional package 'bleak' is not installed (use USB or virtual mode).", flush=True)
            return
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
            except (BleakError, ConnectionError, OSError, TimeoutError, ValueError, RuntimeError) as error:
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
        transport = "usb"
        serial_port = args.serial
        no_ble = False
    elif args.no_ble:
        transport = "none"
        serial_port = None
        no_ble = True
    elif getattr(args, "ble", False) or getattr(args, "address", None) is not None:
        transport = "ble"
        serial_port = None
        no_ble = False

    if getattr(args, "address", None) is not None:
        address = args.address

    mute = True if args.mute else (not saved["sounds_enabled"])
    no_notifications = True if args.no_notifications else (not saved["notifications_enabled"])
    no_quota = True if args.no_quota else (not saved["quota_enabled"])
    web_port = None if args.no_web else args.web_port

    return Bridge(
        port=args.port,
        address=address,
        no_ble=no_ble,
        transport=transport,
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
        require_opencode_client=saved["require_opencode_client"],
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


def request_overlay(web_port, visible=True):
    """Show or hide the floating pet overlay through the running local web console."""
    request = Request(
        "http://127.0.0.1:%d/api/overlay" % web_port,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"visible": bool(visible)}).encode("utf-8"),
    )
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=2) as response:
        return json.loads(response.read(65536))


def build_run_command(args, python_executable=None):
    """Build the detached `pc.opencode run` command line from parsed CLI arguments."""
    command = [str(python_executable or sys.executable), "-P", "-m", "pc.opencode", "run"]
    if getattr(args, "port", 19847) != 19847:
        command.extend(["--port", str(args.port)])
    if getattr(args, "address", None):
        command.extend(["--address", str(args.address)])
    if getattr(args, "ble", False):
        command.append("--ble")
    if getattr(args, "serial", None) is not None:
        command.extend(["--serial", str(args.serial)])
    if getattr(args, "no_ble", False):
        command.append("--no-device")
    if getattr(args, "mute", False):
        command.append("--mute")
    if getattr(args, "no_notifications", False):
        command.append("--no-notifications")
    if getattr(args, "no_quota", False):
        command.append("--no-quota")
    if getattr(args, "quota_config", None):
        command.extend(["--quota-config", str(args.quota_config)])
    if getattr(args, "web_port", DEFAULT_WEB_PORT) != DEFAULT_WEB_PORT:
        command.extend(["--web-port", str(args.web_port)])
    if getattr(args, "no_web", False):
        command.append("--no-web")
    if getattr(args, "overlay", False):
        command.append("--overlay")
    if getattr(args, "duration", None) is not None:
        command.extend(["--duration", str(args.duration)])
    return command


def _tail_log_lines(paths, max_lines=6):
    lines = []
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines.extend(line.strip() for line in text.splitlines() if line.strip())
    return lines[-max_lines:]


def _spawn_detached_bridge(command, log_path, err_path, repo_root):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root)
    kwargs = {
        "cwd": str(repo_root),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    else:
        kwargs["start_new_session"] = True
    with open(log_path, "wb") as stdout_handle, open(err_path, "wb") as stderr_handle:
        return subprocess.Popen(command, stdout=stdout_handle, stderr=stderr_handle, **kwargs)


def ensure_running(args, path=None, wait_seconds=8.0, poll_interval=0.15,
                   spawner=None, control_fn=None, overlay_fn=None,
                   clock=time.monotonic, sleeper=time.sleep):
    """Ensure the OpenBuddy bridge is running; start it detached in the background if needed."""
    discovery = Path(path) if path is not None else discovery_path()
    check = control_fn if control_fn is not None else control
    overlay_requester = overlay_fn if overlay_fn is not None else request_overlay

    try:
        health = check(discovery, "health")
    except (OSError, ValueError):
        health = None

    if health is not None:
        if getattr(args, "overlay", False) and health.get("web_port"):
            try:
                overlay_requester(health["web_port"], True)
            except (OSError, ValueError):
                pass
        return {"started": False, "health": health, "log": None}

    repo_root = Path(__file__).resolve().parents[1]
    log_path = discovery.parent / "bridge.log"
    err_path = discovery.parent / "bridge.log.err"
    command = build_run_command(args)
    launcher = spawner if spawner is not None else _spawn_detached_bridge
    process = launcher(command, log_path, err_path, repo_root)

    deadline = clock() + max(0.1, float(wait_seconds))
    while True:
        try:
            health = check(discovery, "health")
            return {"started": True, "health": health, "log": str(log_path)}
        except (OSError, ValueError):
            pass
        if process is not None and process.poll() is not None:
            # Another bridge instance may have won a concurrent start race.
            try:
                health = check(discovery, "health")
                return {"started": False, "health": health, "log": None}
            except (OSError, ValueError):
                tail = _tail_log_lines((err_path, log_path))
                detail = (" — " + " | ".join(tail)) if tail else ""
                raise RuntimeError(
                    "OpenBuddy bridge exited with code %s (log: %s)%s"
                    % (process.returncode, log_path, detail)
                )
        if clock() >= deadline:
            tail = _tail_log_lines((err_path, log_path))
            detail = (" — " + " | ".join(tail)) if tail else ""
            raise RuntimeError(
                "Timed out waiting for OpenBuddy bridge to start (log: %s)%s"
                % (log_path, detail)
            )
        sleeper(poll_interval)


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
    parser.add_argument("command", choices=("run", "status", "stop", "ensure", "sound-test", "notification-test"), nargs="?", default="run")
    parser.add_argument("--port", type=int, default=19847)
    parser.add_argument("--address", help="Bluetooth LE address (switches transport to BLE; requires optional bleak)")
    parser.add_argument("--ble", action="store_true", help="Drive the device over Bluetooth LE (requires optional bleak)")
    parser.add_argument("--serial", metavar="PORT", help="Drive the device over its USB serial port (COMx, or auto)")
    parser.add_argument("--no-device", "--no-ble", dest="no_ble", action="store_true",
                        help="Run the bridge and virtual pet without an ESP32 device")
    parser.add_argument("--mute", action="store_true", help="Disable PC sound notifications")
    parser.add_argument("--no-notifications", action="store_true", help="Disable Windows toast notifications, independently of sound")
    parser.add_argument("--no-quota", action="store_true", help="Do not run the provider quota command")
    parser.add_argument("--quota-config", metavar="FILE", help="Quota configuration (default: %%APPDATA%%/openbuddy/quota.json)")
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT, help="Local web console HTTP port (default: 8765)")
    parser.add_argument("--no-web", action="store_true", help="Disable the local web console")
    parser.add_argument("--overlay", action="store_true", help="Show the floating desktop pet window on startup")
    parser.add_argument("--duration", type=float, help="Stop after this many seconds")
    parser.add_argument("--wait", type=float, default=8.0, help="Seconds to wait for bridge readiness with 'ensure' (default: 8)")
    args = parser.parse_args()
    if (not (args.port == 0 or 1024 <= args.port <= 65535)
            or not (args.web_port == 0 or 1024 <= args.web_port <= 65535)
            or (args.duration is not None and args.duration <= 0)
            or args.wait <= 0):
        parser.error("Invalid port, duration, or wait timeout")
    try:
        if args.command == "run":
            asyncio.run(run(args))
        elif args.command == "ensure":
            result = ensure_running(args, wait_seconds=args.wait)
            web_port = result["health"].get("web_port")
            suffix = " (http://127.0.0.1:%d/)" % web_port if web_port else ""
            if result["started"]:
                print("[openbuddy] Bridge started" + suffix, flush=True)
            else:
                print("[openbuddy] Bridge already running" + suffix, flush=True)
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
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, "Error: %s\n" % error)


if __name__ == "__main__":
    main()
