"""Exercise the USB serial transport without a board."""

import importlib
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "c6lcd"))
firmware = importlib.import_module("buddy_usb")


class FakeConsole:
    def __init__(self):
        self.incoming = b""
        self.written = b""
        self.fail = False

    def read(self):
        if self.fail:
            raise OSError(5)
        data, self.incoming = self.incoming, b""
        return data

    def write(self, data):
        if self.fail:
            raise OSError(5)
        self.written += data

    def replies(self):
        return [json.loads(line) for line in self.written.splitlines()]


def frame(command, request_id=1, **fields):
    return (json.dumps(dict(fields, v=1, id=request_id, cmd=command)) + "\n").encode()


class UsbFirmwareTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        for name, value in (
            ("ticks_diff", lambda a, b: (a - b + (1 << 29)) % (1 << 30) - (1 << 29)),
        ):
            active = patch.object(firmware.time, name, value, create=True)
            active.start()
            self.addCleanup(active.stop)
        self.console = FakeConsole()
        self.buddy = firmware.BuddyUSB(console=self.console)

    def send(self, data):
        self.console.incoming = data
        self.buddy.tick(self.now)

    def test_state_is_acknowledged_and_displayed(self):
        self.send(frame("state", state="working", label="Running tool"))
        self.assertEqual(self.console.replies(), [{"v": 1, "event": "ack", "id": 1, "state": "working"}])
        self.assertEqual((self.buddy.state, self.buddy.label), ("working", "Running tool"))
        self.assertTrue(self.buddy.online(self.now))

    def test_info_is_stored_and_replaced_by_the_next_state(self):
        self.send(frame("state", state="idle", info="0 / 200"))
        self.assertEqual(self.buddy.info, "0 / 200")
        self.send(frame("state", 2, state="idle"))
        self.assertEqual(self.buddy.info, "")

    def test_offline_until_a_command_and_after_the_lease(self):
        self.assertFalse(self.buddy.online(self.now))
        self.send(frame("ping"))
        self.assertTrue(self.buddy.online(self.now + 14999))
        self.assertFalse(self.buddy.online(self.now + 15001))

    def test_status_reports_usb_transport_and_assets(self):
        self.buddy.asset_status = lambda: {"sd": True, "pet": "a" * 64}
        self.send(frame("status", 2147483647))
        reply = self.console.replies()[0]
        self.assertEqual((reply["transport"], reply["hid"], reply["pet"]), ("usb", False, "a" * 64))
        self.assertLessEqual(len(self.console.written), 257)

    def test_keyboard_arming_is_refused(self):
        self.send(frame("hid_arm"))
        reply = self.console.replies()[0]
        self.assertEqual(reply["event"], "error")
        self.assertIsNone(self.buddy.hid_until)
        self.assertFalse(self.buddy.online(self.now))

    def test_invalid_commands_get_an_error_with_their_id(self):
        self.send(frame("state", 7, state="exploding"))
        self.send(frame("shell", 8))
        errors = self.console.replies()
        self.assertEqual([item["event"] for item in errors], ["error", "error"])
        self.assertEqual([item["id"] for item in errors], [7, 8])
        self.assertEqual(self.buddy.state, "idle")

    def test_console_noise_and_fragmented_frames_are_tolerated(self):
        data = b"BLE: connected 0\r\n\xff\xfe\n" + frame("state", state="success")
        for index in range(0, len(data), 5):
            self.send(data[index:index + 5])
        self.assertEqual(self.buddy.state, "success")
        self.assertEqual(len(self.console.replies()), 1)

    def test_unplugged_console_does_not_stop_the_loop(self):
        self.console.fail = True
        self.buddy.tick(self.now)
        self.send(frame("ping"))
        self.assertEqual(self.console.written, b"")

    def test_pairing_and_hid_state_do_not_apply(self):
        self.assertFalse(self.buddy.pairing_open(self.now))
        self.assertIsNone(self.buddy.passkey)
        self.assertEqual(self.buddy.kind, "usb")


class ConsoleTests(unittest.TestCase):
    def console(self, events):
        console = firmware.Console.__new__(firmware.Console)
        console.readable = 1
        console.poll = types.SimpleNamespace(poll=lambda _timeout: events)
        return console

    def test_error_or_hangup_does_not_attempt_a_blocking_read(self):
        for flags in (8, 16, 24):
            stream = io.BytesIO(b"waiting")
            with patch.object(firmware.sys, "stdin", types.SimpleNamespace(buffer=stream)):
                self.assertEqual(self.console([(None, flags)]).read(), b"")
            self.assertEqual(stream.tell(), 0)

    def test_read_is_bounded_even_if_more_data_is_available(self):
        stream = io.BytesIO(b"x" * (firmware.READ_LIMIT + 10))
        with patch.object(firmware.sys, "stdin", types.SimpleNamespace(buffer=stream)):
            self.assertEqual(len(self.console([(None, 1)]).read()), firmware.READ_LIMIT)
        self.assertEqual(stream.tell(), firmware.READ_LIMIT)


if __name__ == "__main__":
    unittest.main()
