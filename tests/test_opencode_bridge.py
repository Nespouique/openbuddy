import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pc.opencode import Bridge, LEASE_SECONDS, Registry, control, load_discovery


def message(source="project-a", sequence=1, state="thinking", **fields):
    return dict(v=1, source=source, sequence=sequence, state=state, label="OpenCode active",
                pending_permissions=0, pending_forms=0, active_sessions=1, **fields)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.registry = Registry(lambda: self.now)

    def test_waiting_priority_across_sources(self):
        self.registry.update(message())
        question = message("project-b", state="question")
        question["pending_forms"] = 1
        self.registry.update(question)
        self.assertEqual(self.registry.current()["state"], "question")
        approval = message("project-c", state="permission")
        approval["pending_permissions"] = 2
        self.registry.update(approval)
        self.assertEqual(self.registry.current(), {"state": "permission", "label": "2 approval(s) pending"})

    def test_expired_sources_do_not_keep_an_old_waiting_state(self):
        self.registry.update(message())
        self.now = LEASE_SECONDS
        self.assertIsNone(self.registry.current())

    def test_old_sequence_does_not_refresh_lease(self):
        self.registry.update(message(sequence=2))
        self.now = LEASE_SECONDS - 1
        self.assertFalse(self.registry.update(message(sequence=1)))
        self.now += 1
        self.assertIsNone(self.registry.current())

    def test_bad_labels_counts_and_states_are_rejected(self):
        for fields in ({"label": "x" * 25}, {"label": "line\nbreak"},
                       {"pending_forms": True}, {"state": "hid_arm"}, {"sequence": -1}):
            value = message()
            value.update(fields)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.registry.update(value)


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "bridge.json"
        self.bridge = Bridge(port=0, no_ble=True, path=self.path, mute=True, no_notifications=True)
        await self.bridge.start()

    async def asyncTearDown(self):
        await self.bridge.close()
        self.directory.cleanup()

    async def request(self, method="POST", path="/state", body=None, token=None, extra=""):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.bridge.port)
        data = json.dumps(message() if body is None else body).encode()
        authorization = self.bridge.token if token is None else token
        headers = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.bridge.port}\r\n"
                   f"Authorization: Bearer {authorization}\r\nContent-Type: application/json\r\n"
                   f"Content-Length: {len(data)}\r\n{extra}\r\n").encode()
        writer.write(headers + data)
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        head, content = response.split(b"\r\n\r\n", 1)
        return int(head.split(b" ")[1]), json.loads(content)

    async def test_authenticated_state_and_health(self):
        status, response = await self.request()
        self.assertEqual((status, response), (200, {"accepted": True}))
        status, health = await self.request("GET", "/health")
        self.assertEqual(health["state"]["state"], "thinking")
        self.assertTrue(health["read_only"])

    async def test_unauthorized_and_browser_origin_are_rejected(self):
        self.assertEqual((await self.request(token="wrong"))[0], 401)
        self.assertEqual((await self.request(extra="Origin: https://example.com\r\n"))[0], 403)
        self.assertIsNone(self.bridge.registry.current())

    async def test_non_state_commands_cannot_be_forwarded(self):
        self.assertEqual((await self.request(body={"cmd": "hid_arm"}))[0], 400)
        self.assertIsNone(self.bridge.registry.current())

    async def test_stop_only_stops_the_bridge(self):
        self.assertEqual((await self.request("POST", "/stop"))[0], 200)
        self.assertTrue(self.bridge.stopped.is_set())

    async def test_second_bridge_cannot_replace_live_discovery(self):
        original = load_discovery(self.path)
        second = Bridge(port=0, no_ble=True, path=self.path, mute=True, no_notifications=True)
        with self.assertRaises(ValueError):
            await second.start()
        await second.close()
        self.assertEqual(load_discovery(self.path), original)

    async def test_closing_removes_only_own_discovery(self):
        await self.bridge.close()
        self.assertFalse(self.path.exists())

    async def test_usb_worker_delivers_states_and_reports_connection(self):
        class FakeUsbLink:
            def __init__(self):
                self.requests = []
                self.disconnected = asyncio.Event()
                self.closed = False

            async def request(self, command, **fields):
                self.requests.append((command, fields))

            def close(self):
                self.closed = True

        link = FakeUsbLink()

        async def open_link(port, on_event):
            return link

        bridge = Bridge(port=0, path=Path(self.directory.name) / "usb.json", mute=True,
                        no_notifications=True, serial_port="auto")
        with patch("pc.opencode.find_serial_port", return_value="COM6"), \
                patch("pc.opencode.UsbLink.open", open_link):
            await bridge.start()
            try:
                bridge.registry.update(message(state="working"))
                for _ in range(40):
                    if bridge.last_delivered:
                        break
                    await asyncio.sleep(0.05)
                self.assertEqual(bridge.last_delivered["state"], "working")
                self.assertEqual(link.requests[0], ("status", {}))
                self.assertTrue(bridge.health()["usb_connected"])
                self.assertFalse(bridge.health()["ble_connected"])
            finally:
                await bridge.close()
        self.assertFalse(bridge.health()["usb_connected"])
        self.assertTrue(link.closed)

    async def test_control_ignores_environment_proxies_for_loopback(self):
        with patch.dict("os.environ", {"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1", "no_proxy": "", "NO_PROXY": ""}):
            health = await asyncio.to_thread(control, self.path, "health")
        self.assertTrue(health["read_only"])


class CommandLineTests(unittest.TestCase):
    def test_status_and_stop_explain_that_no_bridge_is_running(self):
        from pc import opencode

        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "bridge.json"
            for command in ("status", "stop"):
                with self.subTest(command=command), \
                        patch.object(opencode, "discovery_path", return_value=missing), \
                        patch("sys.argv", ["pc.opencode", command]), \
                        patch("sys.stderr") as stderr, \
                        self.assertRaises(SystemExit) as raised:
                    opencode.main()
                self.assertEqual(raised.exception.code, 1)
                written = "".join(call.args[0] for call in stderr.write.call_args_list)
                self.assertIn("No OpenBuddy bridge is running", written)
