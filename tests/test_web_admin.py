import asyncio
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from c6lcd.buddy_protocol import STATES
from pc.opencode import (Bridge, build_bridge_from_args, load_bridge_config,
                         save_bridge_config, validate_bridge_config)
from pc.pet_overlay import OverlayController, select_opencode_window
from pc.pet_store import (BUILTIN_PET_ID, CHROMA_KEY_RGB, discover_packs,
                          resolve_pet, rgb565, rgb565_frame_to_ppm)


def setUpModule():
    # Never run the developer's real quota command from unit tests.
    global isolated_quota
    isolated_quota = tempfile.TemporaryDirectory()
    patcher = patch("pc.quota.config_path", lambda: Path(isolated_quota.name) / "quota.json")
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    unittest.addModuleCleanup(isolated_quota.cleanup)


def create_synthetic_pack(root, pet_id="demo-pet"):
    pack_dir = Path(root) / pet_id
    pack_dir.mkdir(parents=True, exist_ok=True)
    width, height = 2, 2
    bg = [8, 12, 20]
    bg565 = rgb565(*bg)
    fg565 = rgb565(248, 0, 0)
    frame = struct.pack(">4H", bg565, fg565, fg565, bg565)
    clip_bytes = frame * 2
    (pack_dir / "idle.rgb565").write_bytes(clip_bytes)
    manifest = {
        "version": 1,
        "id": pet_id,
        "format": "RGB565-BE",
        "width": width,
        "height": height,
        "background": bg,
        "states": {state: "idle" for state in STATES + ("offline", "pairing")},
        "source": {"author": "unit-test"},
        "animations": {
            "idle": {
                "file": "idle.rgb565",
                "frames": 2,
                "frame_ms": 260,
                "bytes": len(clip_bytes),
            }
        },
    }
    (pack_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pack_dir, clip_bytes, bg565


class FakeOverlay:
    def __init__(self):
        self.visible = False
        self.error = None
        self.closed = False

    def status(self):
        return {"visible": self.visible, "error": self.error}

    def show(self):
        self.visible = True
        return True

    def hide(self):
        self.visible = False
        return True

    def set_visible(self, visible):
        self.visible = bool(visible)
        return self.visible

    def close(self):
        self.visible = False
        self.closed = True


class PetStoreTests(unittest.TestCase):
    def test_discover_and_decode_ppm_with_chroma_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, clip_bytes, bg565 = create_synthetic_pack(tmp, "mini-pet")
            packs = discover_packs([tmp])
            self.assertIn("mini-pet", packs)
            active_id, pack, _ = resolve_pet("auto", [tmp])
            self.assertEqual(active_id, "mini-pet")
            self.assertIsNotNone(pack)

            ppm = rgb565_frame_to_ppm(clip_bytes[:8], 2, 2, transparent_rgb565=bg565)
            header, pixels = ppm.split(b"\n255\n", 1)
            self.assertEqual(header, b"P6\n2 2")
            self.assertEqual(len(pixels), 12)
            # Pixel 0 and 3 match bg565 -> chroma key; pixel 1 and 2 are red.
            self.assertEqual(tuple(pixels[0:3]), CHROMA_KEY_RGB)
            self.assertEqual(tuple(pixels[3:6]), (255, 0, 0))
            self.assertEqual(tuple(pixels[9:12]), CHROMA_KEY_RGB)

            active_builtin, builtin_pack, _ = resolve_pet(BUILTIN_PET_ID, [tmp])
            self.assertEqual(active_builtin, BUILTIN_PET_ID)
            self.assertIsNone(builtin_pack)

    def test_overlay_controller_reports_isolated_desktop(self):
        controller = OverlayController(
            web_url_provider=lambda: "http://127.0.0.1:8765",
            desktop_checker=lambda: "exebox-sandbox",
        )
        self.assertTrue(controller.status()["desktop_isolated"])
        self.assertFalse(controller.show())
        self.assertFalse(controller.visible)
        self.assertIn("bureau isolé", controller.error)

    def test_select_opencode_window_prioritizes_opencode_host_then_vscode_or_terminal(self):
        # Case 1: VS Code hosts pwsh -> opencode.exe, even if Chrome is higher in Z-order.
        procs = {
            100: (1, "chrome.exe"),
            200: (1, "Code.exe"),
            210: (200, "pwsh.exe"),
            220: (210, "opencode.exe"),
            300: (1, "WindowsTerminal.exe"),
        }
        windows = [
            (10, 100, "OpenBuddy Studio - Chrome"),
            (30, 300, "PowerShell"),
            (20, 200, "openbuddy - Visual Studio Code"),
        ]
        self.assertEqual(select_opencode_window(windows, procs, self_pid=999), 20)

        # Case 2: Detached opencode.exe (parent exited) -> prefers window titled OpenCode, else VS Code.
        detached_procs = {
            100: (1, "chrome.exe"),
            200: (1, "Code.exe"),
            300: (1, "WindowsTerminal.exe"),
            400: (9999, "opencode.exe"),
        }
        windows_with_title = [
            (10, 100, "OpenBuddy Studio - Chrome"),
            (20, 200, "notes - Visual Studio Code"),
            (30, 300, "OpenCode — PowerShell"),
        ]
        self.assertEqual(select_opencode_window(windows_with_title, detached_procs, self_pid=999), 30)

        windows_fallback = [
            (10, 100, "Docs - Chrome"),
            (20, 200, "openbuddy - Visual Studio Code"),
            (30, 300, "PowerShell"),
        ]
        self.assertEqual(select_opencode_window(windows_fallback, detached_procs, self_pid=999), 20)


class BridgeConfigTests(unittest.TestCase):
    def test_config_validation_and_cli_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_file = Path(tmp) / "bridge-config.json"
            save_bridge_config(cfg_file, {
                "transport": "usb",
                "serial_port": "COM9",
                "ble_address": "",
                "sounds_enabled": False,
                "notifications_enabled": True,
                "quota_enabled": False,
                "pet": "builtin",
                "overlay_scale": 2,
            })
            loaded = load_bridge_config(cfg_file)
            self.assertEqual(loaded["transport"], "usb")
            self.assertEqual(loaded["serial_port"], "COM9")
            self.assertFalse(loaded["sounds_enabled"])
            self.assertEqual(loaded["overlay_scale"], 2)

            args = SimpleNamespace(
                port=19847, address=None, serial=None, no_ble=False,
                mute=False, no_notifications=False, no_quota=False,
                quota_config=None, web_port=8765, no_web=False, overlay=False,
            )
            bridge = build_bridge_from_args(args, cfg_path=cfg_file)
            self.assertEqual(bridge.transport, "usb")
            self.assertEqual(bridge.serial_port, "COM9")
            self.assertEqual(bridge.web_port_requested, 8765)
            self.assertFalse(bridge.notifier.enabled)
            self.assertTrue(bridge.no_quota)
            self.assertEqual(bridge.pet_selection, "builtin")
            self.assertEqual(bridge.overlay_scale, 2)

            # Explicit --no-ble overrides saved USB transport.
            args.no_ble = True
            bridge_virtual = build_bridge_from_args(args, cfg_path=cfg_file)
            self.assertEqual(bridge_virtual.transport, "none")

        with self.assertRaises(ValueError):
            validate_bridge_config({"transport": "wifi"})
        with self.assertRaises(ValueError):
            validate_bridge_config({"overlay_scale": 3})
        with self.assertRaises(ValueError):
            validate_bridge_config({"unknown_field": 1})


class WebAdminIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.discovery = Path(self.tmp.name) / "bridge.json"
        self.config_file = Path(self.tmp.name) / "bridge-config.json"
        self.pets_root = Path(self.tmp.name) / "pets"
        _, self.clip_bytes, _ = create_synthetic_pack(self.pets_root, "test-pet")
        self.overlay = FakeOverlay()
        self.bridge = Bridge(
            port=0,
            no_ble=True,
            path=self.discovery,
            mute=True,
            no_notifications=True,
            no_quota=True,
            web_port=0,
            config_path=self.config_file,
            pet_roots=[self.pets_root],
            overlay_controller=self.overlay,
            port_lister=lambda: [{"device": "COM42", "description": "Mock ESP32", "espressif": True}],
        )
        await self.bridge.start()
        self.base = "http://127.0.0.1:%d" % self.bridge.web_port
        self.opener = build_opener(ProxyHandler({}))

    async def asyncTearDown(self):
        await self.bridge.close()
        self.tmp.cleanup()

    async def _get(self, path):
        def call():
            with self.opener.open(self.base + path, timeout=3) as resp:
                return resp.status, resp.read()
        return await asyncio.to_thread(call)

    async def _post(self, path, payload, headers=None):
        def call():
            hdrs = {"Content-Type": "application/json"}
            if headers:
                hdrs.update(headers)
            req = Request(self.base + path, data=json.dumps(payload).encode("utf-8"), headers=hdrs, method="POST")
            with self.opener.open(req, timeout=3) as resp:
                return resp.status, json.loads(resp.read())
        return await asyncio.to_thread(call)

    async def test_page_dashboard_pet_clip_and_ports(self):
        status, html = await self._get("/")
        self.assertEqual(status, 200)
        text = html.decode("utf-8")
        self.assertIn("OpenBuddy Studio", text)
        self.assertIn("Keep the pet visible", text)

        status, raw = await self._get("/api/dashboard")
        self.assertEqual(status, 200)
        dash = json.loads(raw)
        self.assertEqual(dash["bridge"]["web_port"], self.bridge.web_port)
        self.assertEqual(dash["pet"]["id"], "test-pet")
        self.assertFalse(dash["overlay"]["visible"])

        status, clip = await self._get("/api/pet/test-pet/clip/idle")
        self.assertEqual(status, 200)
        self.assertEqual(clip, self.clip_bytes)

        status, ports_raw = await self._get("/api/ports")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(ports_raw)["ports"][0]["device"], "COM42")

    async def test_config_update_persists_and_manual_state_and_overlay(self):
        status, data = await self._post("/api/config", {
            "transport": "none",
            "pet": "builtin",
            "overlay_scale": 2,
            "sounds_enabled": False,
            "notifications_enabled": False,
            "quota_enabled": False,
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["config"]["pet"], "builtin")
        self.assertEqual(data["config"]["overlay_scale"], 2)
        self.assertTrue(self.config_file.is_file())
        saved = load_bridge_config(self.config_file)
        self.assertEqual(saved["pet"], "builtin")
        self.assertEqual(saved["overlay_scale"], 2)

        status, data = await self._post("/api/state", {"state": "thinking", "label": "Demo", "info": "5 / 200"})
        self.assertEqual(status, 200)
        self.assertTrue(data["bridge"]["manual_mode"])
        self.assertEqual(data["bridge"]["effective_state"]["state"], "thinking")
        self.assertEqual(data["bridge"]["effective_state"]["info"], "5 / 200")

        status, data = await self._post("/api/state/clear", {})
        self.assertEqual(status, 200)
        self.assertFalse(data["bridge"]["manual_mode"])
        self.assertIsNone(data["bridge"]["effective_state"])

        status, data = await self._post("/api/overlay", {"visible": True})
        self.assertEqual(status, 200)
        self.assertTrue(data["overlay"]["visible"])
        self.assertTrue(self.overlay.visible)

        status, data = await self._post("/api/overlay", {"toggle": True})
        self.assertEqual(status, 200)
        self.assertFalse(data["overlay"]["visible"])

    async def test_security_rejections(self):
        with self.assertRaises(HTTPError) as err:
            await self._post("/api/state", {"state": "idle"}, headers={"Origin": "http://example.invalid"})
        self.assertEqual(err.exception.code, 403)

        with self.assertRaises(HTTPError) as err:
            await self._post("/api/state", {"state": "invalid-state"})
        self.assertEqual(err.exception.code, 400)

        with self.assertRaises(HTTPError) as err:
            await self._get("/api/pet/test-pet/clip/../manifest")
        self.assertEqual(err.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
